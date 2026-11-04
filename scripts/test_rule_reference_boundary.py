"""A preparation board is organisation, not the rules played at the table.

Every generation message used to carry the full board profile, including the published
game it borrows methods from (name, "rule archive" summary, risk axes, GM/failure moves,
scene guidance). Those fields invite target-system mechanics the source never states.
These checks pin the boundary on the real message builders, using synthetic COC-style
and DND-style source material.
"""
from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402
from backend.app.llm import FakeLLM, LLMClient  # noqa: E402
from backend.domain import (  # noqa: E402
    DerivedCard,
    DomainValidationError,
    ExampleBundle,
    SourceFact,
    SourceRef,
    load_profiles,
    validate_bundle,
)

PROFILES = load_profiles(ROOT / "backend" / "domain" / "profiles")

# Rule-bearing board metadata that must never reach a generation message. Card field
# names such as gm_moves or positioning are deliberately not listed: they are part of
# the card contract, not board metadata.
FORBIDDEN_BOARD_KEYS = {
    "summary", "risk_axes", "failure_moves", "gm_moves", "prompts", "scene_guidance",
    "name", "version",
}
# Published game names identify leakage wherever they appear.
FORBIDDEN_TEXT = ("规则档案", "Cthulhu Dark", "Daggerheart")
ALLOWED_BOARD_KEYS = {"id", "profile_kind", "runtime_navigation_mode", "runtime_anchor_type"}

COC_FACT = {
    "id": "fact_coc_1",
    "kind": "threat",
    "text": "守秘人掷 1D100 判定，成功率 55%；失败时理智损失 1/1D4。",
}
SCENE_FACT = {
    "id": "fact_watch",
    "kind": "location",
    "text": "仓库有6名看守，每10分钟换岗；东门在20:30关闭，西侧有2条退路。",
}
DND_FACT = {
    "id": "fact_dnd_1",
    "kind": "threat",
    "text": "守门卫兵 AC 15，22 点生命值，挑战等级 3，携带长戟。",
}


def message_text(messages: list[dict]) -> str:
    return "\n".join(str(message.get("content", "")) for message in messages)


def assert_boundary(messages: list[dict], label: str) -> str:
    text = message_text(messages)
    assert artifacts.BOARD_REFERENCE_BOUNDARY.strip() in text, f"{label} lacks the board boundary"
    for forbidden in FORBIDDEN_TEXT:
        assert forbidden not in text, f"{label} names a reference ruleset: {forbidden}"
    board_match = None
    for line in text.splitlines():
        if line.startswith("BOARD_PROFILE_JSON="):
            board_match = line.split("=", 1)[1]
            break
    assert board_match, f"{label} carries no board payload"
    board = json.loads(board_match)
    assert set(board) <= ALLOWED_BOARD_KEYS | {"card_definitions"}, f"{label} board keys: {board}"
    assert not (set(board) & FORBIDDEN_BOARD_KEYS), f"{label} leaks board metadata: {set(board)}"
    assert "keep supplied numbers only" not in text, f"{label} restricts narrative quantities"
    assert "non-mechanical" in text, f"{label} must distinguish narrative quantities from rules"
    return text


def check_digest() -> str:
    profile = PROFILES["cthulhu-dark-2e"]
    messages = artifacts._local_digest_messages(
        profile, [COC_FACT, DND_FACT, SCENE_FACT]
    )
    text = assert_boundary(messages, "local digest")
    # Source rules text stays available to the model instead of being rewritten.
    assert "1D100" in text and "AC 15" in text
    return text


def check_plan() -> str:
    profile = PROFILES["daggerheart"]
    units = [{"kind": "location", "title": "峡谷", "fact_ids": ["fact_coc_1", "fact_dnd_1"]}]
    messages = artifacts._global_plan_messages(
        profile,
        units,
        {"fact_coc_1": 80, "fact_dnd_1": 60},
        {"fact_coc_1": 40, "fact_dnd_1": 30},
    )
    text = assert_boundary(messages, "global plan")
    # The planner still receives the card categories it must choose from.
    for card_type in ("environment", "npc", "enemy", "encounter_clock"):
        assert card_type in text
    return text


def check_materialize() -> str:
    profile = PROFILES["daggerheart"]
    plan = {
        "type": "environment",
        "title": "峡谷伏击点",
        "fact_ids": ["fact_coc_1", "fact_dnd_1"],
        "fields": {},
    }
    messages = artifacts._materialization_messages(profile, plan, [COC_FACT, DND_FACT])
    text = assert_boundary(messages, "materialize")
    # The card contract is still explicit, so generation does not have to guess fields.
    assert "CARD_DEFINITION_JSON" in text and "environment_type" in text
    assert "AC 15" in text
    return text


def check_draft() -> str:
    profile = PROFILES["cthulhu-dark-2e"]
    bundle = ExampleBundle(
        id="rule_boundary_fixture",
        name="规则边界夹具",
        profile_ids=["cthulhu-dark-2e"],
        facts=[],
        cards=[],
    )
    messages = artifacts._prompt_messages(bundle, profile, [COC_FACT, DND_FACT])
    text = assert_boundary(messages, "draft")
    assert "ALLOWED_CARD_DEFINITIONS_JSON" in text and "location" in text
    assert "1D100" in text
    return text


def check_validation() -> None:
    # A DND-style card carrying only source-backed numbers and narrative fields is valid:
    # no target-system tier, difficulty or adversary type is required anywhere.
    card = DerivedCard(
        id="card_dnd_guard",
        profile_id="daggerheart",
        type="enemy",
        title="守门卫兵",
        fact_ids=[DND_FACT["id"]],
        fields={
            "motives": ["守住关隘，不让没有凭证的人通过。"],
            "current_objective": "弄清来人的身份和目的。",
            "signature_actions": ["横戟拦住去路", "吹哨叫来巡夜人"],
            "danger_signs": "把手按在长戟上，身体侧向门里。",
            "damaged_behavior": "退到门内，只守不退。",
            "resolutions": ["出示通行凭证", "被说服后放行", "被制服"],
            "source_statblock": "AC 15；22 点生命值；挑战等级 3。",
        },
        edit_state="approved",
    )
    card.field_sources = {field: [DND_FACT["id"]] for field in card.fields}
    bundle = ExampleBundle(
        id="rule_boundary_fixture",
        name="规则边界夹具",
        profile_ids=["daggerheart"],
        facts=[source_fact(DND_FACT)],
        cards=[card],
    )
    validate_bundle(bundle, PROFILES)
    assert card.fields["source_statblock"] == "AC 15；22 点生命值；挑战等级 3。"

    # Narrative requirements still hold: the contract never asks for target-system data,
    # but it does ask for the material a GM reads at the table.
    missing = card.model_copy(deep=True)
    missing.fields = {"source_statblock": "AC 15"}
    try:
        validate_bundle(
            ExampleBundle(
                id="rule_boundary_fixture",
                name="规则边界夹具",
                profile_ids=["daggerheart"],
                facts=[source_fact(DND_FACT)],
                cards=[missing],
            ),
            PROFILES,
        )
    except DomainValidationError as error:
        assert "required fields" in str(error), error
    else:
        raise AssertionError("a card without its narrative fields must still be rejected")

    # Profile metadata no longer presents a published game as the running rules.
    for profile_id in ("cthulhu-dark-2e", "daggerheart", "module-prep"):
        summary = PROFILES[profile_id].summary
        assert "规则档案" not in summary, profile_id
        assert PROFILES[profile_id].profile_kind == (
            "prep" if profile_id == "module-prep" else "runtime"
        )


def source_fact(raw: dict) -> SourceFact:
    return SourceFact(
        **raw, visibility="explicit",
        source_refs=[SourceRef(file="fixture://rule-boundary", page=1)],
    )


def check_corrections_and_cache() -> None:
    """Exercise actual step validation/correction, caching and JSON format retry."""
    class RejectOnce:
        def __init__(self):
            self.calls = []
            self.fake = FakeLLM({"model": "boundary-fixture"})

        def chat_json(self, messages, **kwargs):
            self.calls.append(deepcopy(messages))
            if len(self.calls) == 1:
                return {}
            return self.fake.chat_json(messages, **kwargs)

    with tempfile.TemporaryDirectory() as directory, patch.object(
        storage, "DB_PATH", Path(directory) / "boundary.db"
    ), patch.object(storage, "UPLOAD_DIR", Path(directory) / "uploads"):
        storage.init_db()
        for profile_id in ("cthulhu-dark-2e", "daggerheart"):
            profile = PROFILES[profile_id]
            facts = [source_fact(raw) for raw in (SCENE_FACT, COC_FACT, DND_FACT)]
            payload = artifacts._fact_payload_from_facts(facts)
            bundle = ExampleBundle(
                id="boundary_" + profile_id.replace("-", "_"), name="Boundary",
                profile_ids=[profile_id], facts=facts, cards=[],
            )
            job, _ = artifacts.create_artifact_job(
                bundle.id, profile_id, model_id="boundary-fixture", fake_model=True
            )

            def corrected(stage, messages, validator):
                client = RejectOnce()
                kwargs = dict(
                    stage=stage, step_index=1,
                    input_payload={"version": artifacts.PROMPT_VERSION, "messages": messages},
                    input_refs={"fact_ids": [fact.id for fact in facts]},
                    messages=messages, client=client, max_tokens=8000, validator=validator,
                )
                output = artifacts._run_json_step(job, **kwargs)
                assert len(client.calls) == 2, (profile_id, stage)
                for request in client.calls:
                    text = assert_boundary(request, stage)
                    assert "6名看守" in text and "10分钟" in text
                assert "deterministic validation" in client.calls[1][-1]["content"]
                assert client.calls[1][:len(messages)] == messages
                assert artifacts._run_json_step(job, **kwargs) == output
                assert len(client.calls) == 2, "a successful step must be reused"
                return output

            digest = corrected(
                "local_digest", artifacts._local_digest_messages(
                    profile, payload
                ),
                lambda raw: artifacts._validate_local_digest(raw, facts, batch_index=1),
            )
            units = digest["units"]
            plan = corrected(
                "global_plan", artifacts._global_plan_messages(
                    profile, units,
                    {fact.id: len(fact.text) for fact in facts},
                    {fact.id: len(fact.text) for fact in facts},
                ),
                lambda raw: artifacts._validate_global_plan(raw, units, facts, profile, bundle),
            )
            planned = plan["cards"][0]
            corrected(
                "materialize", artifacts._materialization_messages(profile, planned, payload),
                lambda raw: artifacts._validate_materialized_card(
                    raw, bundle, profile, planned, model_id="boundary-fixture"
                ),
            )
            direct_messages = artifacts._prompt_messages(bundle, profile, payload)
            output = corrected(
                "direct_generation", direct_messages,
                lambda raw: artifacts._validated_card_output(
                    raw, bundle, profile, model_id="boundary-fixture"
                ),
            )
            if profile_id == "cthulhu-dark-2e":
                location = next(card for card in output["cards"] if card["type"] == "location")
                assert "6名看守" in location["fields"]["normal_state"]
                assert "10分钟" in location["fields"]["arrival_description"]
                assert location["field_sources"]["normal_state"]
                assert bundle.facts[1].text == COC_FACT["text"]

            # The actual network client's parser retries with the same board/source
            # messages too. Override chat so no network or worker process is used.
            client = LLMClient.__new__(LLMClient)
            responses = iter(["invalid json", '{"ok":true}'])
            requests = []

            def chat(messages, **kwargs):
                requests.append(deepcopy(messages))
                return next(responses)

            client.chat = chat
            assert client.chat_json(direct_messages) == {"ok": True}
            assert len(requests) == 2
            assert requests[1][:len(direct_messages)] == direct_messages
            assert_boundary(requests[1], "JSON format retry")


def main() -> None:
    digest = check_digest()
    plan = check_plan()
    materialize = check_materialize()
    draft = check_draft()
    check_validation()
    check_corrections_and_cache()
    # One shared statement, not four different prohibitions.
    statement = artifacts.BOARD_REFERENCE_BOUNDARY.strip()
    for text in (digest, plan, materialize, draft):
        assert text.count(statement) == 1
    for profile_id in ("cthulhu-dark-2e", "daggerheart"):
        payload = artifacts._board_prompt_payload(PROFILES[profile_id])
        assert set(payload) == {
            "id", "profile_kind", "runtime_navigation_mode", "runtime_anchor_type"
        }, payload
    print("PASS: generation messages carry the board structure and one reference boundary, never rule mechanics")


if __name__ == "__main__":
    main()
