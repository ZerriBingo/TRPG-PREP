"""The compact planning contract: one producer, the 32k policy, v24 identity, validation.

These checks drive the production entry points: the message producer, the split window
recovery and the two validators. Nothing here sends a request.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "split_recovery", ROOT / "scripts" / "test_global_plan_split_recovery.py"
)
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


def check_one_producer() -> None:
    """Scope, window and leaf all leave the same producer, checked on real requests."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    seen: list[list[dict]] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        seen.append(messages)
        if len(split._units_in(messages)) > 1:
            return split._length_frame()
        return split._plan_frame(messages, "叶子")

    job = type("J", (), {"id": "artifact_job_one_producer", "model_id": "probe",
                         "fake_model": False})()
    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))

    window_sends = [messages for messages in seen if len(split._units_in(messages)) > 1]
    leaf_sends = [messages for messages in seen if len(split._units_in(messages)) == 1]
    assert window_sends and leaf_sends, [len(split._units_in(m)) for m in seen]

    window_text = window_sends[0][0]["content"]
    leaf_text = leaf_sends[0][0]["content"]
    # The multi-unit window is the compact window contract, not the long scope contract.
    assert "ONE WINDOW made of several supplied index units" in window_text
    assert "Output limits, read from the validator" in window_text
    assert "assumed_state" in window_text
    # The leaf is the same producer with the leaf addendum; both carry the shared rules.
    assert "ONE WINDOW made of several supplied index units" not in leaf_text
    assert "cannot split further" in leaf_text
    for text in (window_text, leaf_text):
        assert "Keep each entity and its own facts together" in text
        assert "Output limits, read from the validator" in text

    # The bytes are exactly what the producer builds for that window, fact order included.
    ids = {fid for unit in units for fid in unit.get("fact_ids", [])}
    expected = artifacts._global_plan_messages(
        profile,
        units,
        {key: value for key, value in sizes.items() if key in ids},
        {key: value for key, value in tokens.items() if key in ids},
        None,
        partial_window=True,
        lead_window=True,
        compact_window=True,
        fact_payload=artifacts._fact_payload_from_facts(
            [fact for fact in facts if fact.id in ids]
        ),
    )
    assert json.dumps(window_sends[0], ensure_ascii=False) == json.dumps(
        expected, ensure_ascii=False
    ), "the window request must be the producer's message"


def check_schema_bounds() -> None:
    """The compact bounds state the keys and shapes the validator will enforce."""
    bounds = artifacts._schema_bounds(
        artifacts.GlobalPlanResponsePayload.model_json_schema()
    )
    root = bounds["<root>"]
    assert root["required"] == ["cards"] and root["extra"] == "forbidden", root
    assert "cards" in root["keys"], root
    card = bounds["cards[]"]
    assert card["required"] == ["type", "title", "purpose", "fact_ids"], card
    assert {"title", "purpose", "situation", "case_scope"} <= set(card["keys"]), card
    assert bounds["cards[].title"]["maxLength"] == 160
    brief = bounds["brief"]
    assert brief["required"] == ["premise", "current_situation"], brief
    assert brief["shape"] == "object", brief
    assert bounds["brief.field_sources"]["shape"] == "object", bounds["brief.field_sources"]
    assert bounds["brief.field_sources{}"]["shape"] == "array", bounds["brief.field_sources{}"]
    chapter = bounds["chapter_briefs[]"]
    assert {"chapter_id", "brief", "reference_fact_ids"} <= set(chapter["keys"]), chapter
    assert chapter["required"] == ["chapter_id", "brief"], chapter


def check_budget_policy() -> None:
    """A multi-unit request starts at 32k with no raise; a leaf keeps 12k -> 16k."""
    assert artifacts.GLOBAL_PLAN_MULTI_UNIT_MAX_TOKENS == 32000
    assert artifacts.GLOBAL_PLAN_MAX_TOKENS == 12000
    assert artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS == 16000
    assert artifacts._global_plan_shared_request_limit(22) == 37
    assert artifacts._global_plan_shared_request_limit(64) == 64


def check_window_budgets_in_the_real_path() -> None:
    """The split really sends 32k for a window and [12k, 16k] for its leaves."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    seen: list[tuple[tuple[str, ...], int]] = []

    attempts: dict[tuple[str, ...], int] = {}

    def worker(messages, temperature, max_tokens, **kwargs):
        ids = tuple(unit["id"] for unit in split._units_in(messages))
        seen.append((ids, max_tokens))
        attempts[ids] = attempts.get(ids, 0) + 1
        if len(ids) > 1 or attempts[ids] == 1:
            # A window runs out of budget; each leaf spends its 12000 the same way and is
            # asked once more at its own 16000.
            return split._length_frame()
        return split._plan_frame(messages, f"窗口 {len(seen)}")

    job = type("J", (), {"id": "artifact_job_compact_contract", "model_id": "probe",
                         "fake_model": False})()
    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))

    multi = [budget for ids, budget in seen if len(ids) > 1]
    assert multi and all(budget == 32000 for budget in multi), seen
    for plan_id in [unit["id"] for unit in units]:
        own = [budget for ids, budget in seen if ids == (plan_id,)]
        assert own == [12000, 16000], (plan_id, own, seen)


def check_single_unit_windows_are_measured_as_leaves() -> None:
    """A one-unit window is sent as a leaf, and the sent message is byte-identical to it."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    with mock.patch.object(artifacts, "GLOBAL_PLAN_WINDOW_MAX_CHARS", 6000):
        chunks = artifacts._global_plan_window_chunks(
            units, profile, sizes, tokens, None, None, facts=facts
        )
    assert [len(chunk) for chunk in chunks] == [1, 1], chunks

    sent: list[str] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        if len(split._units_in(messages)) > 1:
            return split._length_frame()
        sent.append(json.dumps(messages, ensure_ascii=False))
        return split._plan_frame(messages, "叶子")

    job = type("J", (), {"id": "artifact_job_single_unit", "model_id": "probe",
                         "fake_model": False})()
    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        with mock.patch.object(artifacts, "GLOBAL_PLAN_WINDOW_MAX_CHARS", 6000):
            split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))

    assert len(sent) == 2, sent
    for index, unit in enumerate(units):
        ids = set(unit.get("fact_ids", []))
        owned = [fact for fact in facts if fact.id in ids]
        expected = artifacts._global_plan_messages(
            profile,
            [unit],
            {key: value for key, value in sizes.items() if key in ids},
            {key: value for key, value in tokens.items() if key in ids},
            None,
            partial_window=True,
            lead_window=index == 0,
            leaf_window=True,
            fact_payload=artifacts._fact_payload_from_facts(owned),
        )
        assert sent[index] == json.dumps(expected, ensure_ascii=False), (
            "the leaf request must be exactly the measured message"
        )


def check_version_identity() -> None:
    """An older release identity never answers this one; an exact success is reused."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    calls = {"n": 0}

    def worker(messages, temperature, max_tokens, **kwargs):
        calls["n"] += 1
        return split._plan_frame(messages, f"窗口 {calls['n']}")

    job = type("J", (), {"id": "artifact_job_compact_version", "model_id": "probe",
                         "fake_model": False})()
    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        assert calls["n"] >= 1
        paid = calls["n"]
        # Same production version: the stored plan is reused with no request.
        split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        assert calls["n"] == paid, (paid, calls["n"])
        # A different version identity must not reuse it.
        with mock.patch.object(artifacts, "GLOBAL_PLAN_PROMPT_VERSION",
                               "prep-artifact-global-plan-v22"):
            split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        assert calls["n"] > paid, "an older version must not answer a new request"


def check_parent_validators_are_strict() -> None:
    """Missing coverage or overview, a long situation and an extra field all fail."""
    # A card type only exists in the profile that defines it, so this check runs on the
    # reality-horror board the contract is for.
    profile, facts, units, sizes, tokens, bundle, context, _chapter = split._board_fixture(
        "cthulhu-dark-2e"
    )
    all_facts = [fact.id for fact in facts]
    card = {
        "type": "location", "title": "地点卡", "purpose": "入口。",
        "fact_ids": all_facts, "chapter_ids": [], "focus": [], "open_questions": [],
        "runtime_groups": [], "story_beat_ids": [], "situation": "当前",
        "case_scope": "current",
    }
    overview = {
        "type": "chapter_overview", "title": "概览", "purpose": "本范围的情势。",
        "fact_ids": all_facts, "chapter_ids": [],
    }
    good = {"cards": [card, overview], "open_questions": []}
    artifacts._validate_global_plan(good, units, facts, profile, bundle, None)

    # Coverage is enforced where the board owns chapters; a board without chapters
    # reports uncovered facts instead of failing, which is the product's own contract.
    chapter_profile, chapter_facts, chapter_units, chapter_sizes, chapter_tokens,         chapter_bundle, chapter_context, chapter_id = split._board_fixture(
            "cthulhu-dark-2e", chapters=True
        )
    chapter_card = {
        "type": "location", "title": "地点卡", "purpose": "入口。",
        "fact_ids": [chapter_facts[0].id], "chapter_ids": [chapter_id], "focus": [],
        "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
        "situation": "当前", "case_scope": "current",
    }
    try:
        artifacts._validate_global_plan(
            {"cards": [chapter_card], "chapter_briefs": [
                {"chapter_id": chapter_id,
                 "brief": {"premise": "前提", "current_situation": "现状"},
                 "reference_fact_ids": [chapter_facts[0].id]}
            ], "open_questions": []},
            chapter_units, chapter_facts, chapter_profile, chapter_bundle,
            {chapter_id},
        )
    except Exception:
        pass
    else:
        raise AssertionError("a chapter board must reject uncovered facts")

    for name, payload in (
        ("missing overview", {"cards": [card], "open_questions": []}),
        ("long situation", {"cards": [{**card, "situation": "长" * 161}, overview],
                            "open_questions": []}),
        ("extra assumed_state", {"cards": [{**card, "assumed_state": "假定"}, overview],
                                 "open_questions": []}),
    ):
        try:
            artifacts._validate_global_plan(payload, units, facts, profile, bundle, None)
        except Exception:
            continue
        raise AssertionError(f"{name} must fail the parent validation")


# The v24 duties live in one shared contract, so every contract production builds must
# carry all of them: closure, source conditions, resource destinations, handout use.
SHARED_DUTIES = (
    "never blanket support for the sentence it is attached to",
    "the overview's own assertions follow the same rule",
    "keep it in open_questions rather than writing an unsupported assertion",
    "having actually told the investigators are four different states",
    "what an opponent does after noticing or hearing about it are",
    "what only a successful check yields",
    "with the source's own check and numbers",
    "being seen escaping is not merely departing",
    "a newspaper branch never decides whether an event takes place",
    "conditional_example remains reserved for the source's own play",
    "a general verification route may hang on the person or place",
    "merely because its source kind is a handout",
)
# Location-only duties: an environment board keeps one runnable situation per card, so
# these sentences must not reach it.
LOCATION_DUTIES = (
    "Two different institutions never share one location card",
    "serve the same purpose, and cite completely identical fact_ids",
    "keeps its stated purpose without inventing a building",
    "is not an organized usable resource",
)


def check_shared_contract_in_every_planning_contract() -> None:
    """Root, window and leaf all carry the shared v24 duties on both boards."""
    for board in ("cthulhu-dark-2e", "daggerheart"):
        profile, facts, units, sizes, tokens, _bundle, context, _chapter = split._board_fixture(
            board
        )
        # A multi-unit root scope is sent with the same compact window contract the
        # recovery uses (checked on real sends below); a single-unit root is a leaf.
        contracts = {
            "window": {"partial_window": True, "compact_window": True},
            "leaf": {"partial_window": True, "leaf_window": True},
        }
        for label, kwargs in contracts.items():
            messages = artifacts._global_plan_messages(
                profile, units[:1], sizes, tokens, context,
                fact_payload=artifacts._fact_payload_from_facts(facts[:1]), **kwargs,
            )
            text = messages[0]["content"]
            for duty in SHARED_DUTIES:
                assert duty in text, (board, label, duty)
            for duty in LOCATION_DUTIES:
                assert (duty in text) == (board == "cthulhu-dark-2e"), (board, label, duty)


def check_sent_requests_carry_the_shared_contract() -> None:
    """The requests production really sends carry the duties, not only the index path."""
    anchor = {"cthulhu-dark-2e": "location", "daggerheart": "environment"}
    for board in ("cthulhu-dark-2e", "daggerheart"):
        profile, facts, units, sizes, tokens, bundle, context, _chapter = split._board_fixture(
            board
        )
        sent: list[list[dict]] = []
        has_overview = any(
            definition.type == "chapter_overview" for definition in profile.card_definitions
        )

        def worker(messages, temperature, max_tokens, **kwargs):
            sent.append(messages)
            if len(split._units_in(messages)) > 1:
                return split._length_frame()
            fact_ids = [fid for unit in split._units_in(messages) for fid in unit["fact_ids"]]
            cards = [{
                "type": anchor[board], "title": "叶子：" + split._units_in(messages)[0]["id"],
                "purpose": "本窗口可调用的入口。", "fact_ids": fact_ids, "chapter_ids": [],
                "focus": [], "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
                "situation": "叶子情境", "case_scope": "current",
            }]
            if has_overview:
                cards.append({"type": "chapter_overview", "title": "窗口概览",
                              "purpose": "本窗口事实在整体情势中的位置。",
                              "fact_ids": fact_ids, "chapter_ids": []})
            payload: dict = {"cards": cards, "open_questions": []}
            if board == "daggerheart":
                payload["brief"] = {"premise": "前提", "current_situation": "现状"}
            content = json.dumps(payload, ensure_ascii=False)
            return {"content": content, "finish_reason": "stop", "content_length": len(content)}

        job = type("J", (), {"id": "artifact_job_shared_" + board.replace("-", "_"),
                             "model_id": "probe", "fake_model": False})()
        with tempfile.TemporaryDirectory() as directory:
            storage.DB_PATH = Path(directory) / "test.db"
            storage.init_db()
            with mock.patch.object(artifacts, "GLOBAL_PLAN_WINDOW_MAX_CHARS", 6000):
                output = split._scope_call(job, profile, facts, units, sizes, tokens,
                                           split._client(worker))

        shapes = {len(split._units_in(messages)) for messages in sent}
        assert 1 in shapes and max(shapes) > 1, shapes
        assert {fid for card in output["cards"] for fid in card["fact_ids"]} == {
            fact.id for fact in facts
        }
        for messages in sent:
            text = messages[0]["content"]
            for duty in SHARED_DUTIES:
                assert duty in text, (board, len(split._units_in(messages)), duty)


def _location_card(title: str, purpose: str, fact_ids: list[str]) -> dict:
    return {
        "type": "location", "title": title, "purpose": purpose, "fact_ids": fact_ids,
        "chapter_ids": [], "focus": [], "open_questions": [], "runtime_groups": [],
        "story_beat_ids": [], "situation": "", "case_scope": "current",
    }


def check_independent_institutions_may_share_one_fact() -> None:
    """One complete fact may support two alternative institutions as two entries."""
    profile, facts, units, sizes, tokens, bundle, _context, _chapter = split._board_fixture(
        "cthulhu-dark-2e"
    )
    shared = [fact.id for fact in facts]
    plan = {
        "cards": [
            _location_card("第一处机构", "可查阅档案并核验线索。", shared),
            _location_card("第二处机构", "同一用途的另一处机构，入口独立。", shared),
        ],
        "open_questions": [],
    }
    validated = artifacts._validate_global_plan_window(
        plan, units, facts, profile, bundle, None
    )
    assert [card["title"] for card in validated["cards"]] == ["第一处机构", "第二处机构"]


def check_verification_route_hangs_on_a_person_or_place() -> None:
    """A general verification fact may ride a person or place card's own closure."""
    profile, facts, units, sizes, tokens, bundle, _context, _chapter = split._board_fixture(
        "cthulhu-dark-2e"
    )
    assert "npc" in {definition.type for definition in profile.card_definitions}
    plan = {
        "cards": [
            _location_card("档案馆", "可以调阅档案。", [facts[0].id]),
            {
                "type": "npc", "title": "核对人", "purpose": "可以核验档案真伪。",
                "fact_ids": [facts[0].id], "chapter_ids": [], "focus": [],
                "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
                "situation": "", "case_scope": "current",
            },
        ],
        "open_questions": [],
    }
    validated = artifacts._validate_global_plan_window(
        plan, units, facts, profile, bundle, None
    )
    assert [card["type"] for card in validated["cards"]] == ["location", "npc"]
    assert validated["cards"][0]["fact_ids"] == validated["cards"][1]["fact_ids"]


def check_pure_history_may_stay_a_reference_fact() -> None:
    """A global_reference disposition is satisfied by the reference list, not a card."""
    (
        profile, facts, units, sizes, tokens, bundle, _context, chapter_id,
    ) = split._board_fixture("cthulhu-dark-2e", chapters=True)
    dispositions = [
        disposition.model_copy(update={"kind": "global_reference", "chapter_ids": []})
        if disposition.fact_id == facts[3].id
        else disposition
        for disposition in bundle.fact_dispositions
    ]
    bundle = bundle.model_copy(update={"fact_dispositions": dispositions})
    plan = {
        "cards": [
            {**_location_card("当前场景", "本范围可运行的地点。",
                              [fact.id for fact in facts[:3]]),
             "chapter_ids": [chapter_id]},
            {
                "type": "chapter_overview", "title": "概览", "purpose": "本范围的情势。",
                "fact_ids": [facts[0].id], "chapter_ids": [chapter_id],
            },
        ],
        "chapter_briefs": [
            {"chapter_id": chapter_id,
             "brief": {"premise": "前提", "current_situation": "现状"},
             "reference_fact_ids": [facts[1].id]},
        ],
        "global_reference_fact_ids": [facts[3].id],
        "open_questions": [],
    }
    validated = artifacts._validate_global_plan(
        plan, units, facts, profile, bundle, {chapter_id}
    )
    assert validated["global_reference_fact_ids"] == [facts[3].id]
    assert validated["uncovered_fact_ids"] == []


def check_card_closure_reaches_materialization() -> None:
    """The planner's own fact closure survives the plan projection and materialization."""
    profile, facts, units, sizes, tokens, _bundle, _context, _chapter = split._board_fixture(
        "cthulhu-dark-2e"
    )
    selected = [facts[0].id, facts[2].id]
    plan = {**_location_card("档案室", "可以查阅档案。", selected),
            "situation": "当前"}
    projected = artifacts._prompt_plan(plan)
    assert projected["fact_ids"] == selected
    messages = artifacts._materialization_messages(
        profile, plan, artifacts._fact_payload_from_facts([facts[0], facts[2]])
    )
    carried = json.loads(
        next(line[len("ORIGINAL_FACTS_JSON="):]
             for line in messages[1]["content"].splitlines()
             if line.startswith("ORIGINAL_FACTS_JSON="))
    )
    assert [fact["id"] for fact in carried] == selected
    assert json.loads(
        next(line[len("CARD_PLAN_JSON="):]
             for line in messages[1]["content"].splitlines()
             if line.startswith("CARD_PLAN_JSON="))
    )["fact_ids"] == selected


def check_v23_records_do_not_answer_v24() -> None:
    """A stored v23 success and a stored v23 failure are both re-run under v24."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    job = type("J", (), {"id": "artifact_job_v23_to_v24", "model_id": "probe",
                         "fake_model": False})()
    v24 = "prep-artifact-global-plan-v24"
    v23 = "prep-artifact-global-plan-v23"
    calls = {"n": 0, "fail": False}

    def worker(messages, temperature, max_tokens, **kwargs):
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("offline transport failure")
        return split._plan_frame(messages, f"窗口 {calls['n']}")

    def run():
        return split._scope_call(job, profile, facts, units, sizes, tokens,
                                 split._client(worker))

    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        # A release-identity success is stored first, then the same input under v24.
        with mock.patch.object(artifacts, "GLOBAL_PLAN_PROMPT_VERSION", v23):
            run()
            paid = calls["n"]
            run()
            assert calls["n"] == paid, "an exact v23 success is reused"
        run()
        assert calls["n"] > paid, "a stored v23 success must not answer v24"
        v24_paid = calls["n"]
        run()
        assert calls["n"] == v24_paid, "an exact v24 success is reused"
        # A stored v23 failure must not skip v24's own duties either.
        v23_failure_job = type("J", (), {"id": "artifact_job_v23_failure", "model_id": "probe",
                                         "fake_model": False})()
        calls["fail"] = True
        with mock.patch.object(artifacts, "GLOBAL_PLAN_PROMPT_VERSION", v23):
            try:
                split._scope_call(v23_failure_job, profile, facts, units, sizes, tokens,
                                  split._client(worker))
            except Exception:
                pass
            else:
                raise AssertionError("the offline transport failure must stop the step")
        failed = [
            row for row in storage.list_artifact_job_steps(v23_failure_job.id)
            if row["stage"] == "global_plan"
        ]
        assert failed and all(row["status"] == "failed" for row in failed), failed
        calls["fail"] = False
        before = calls["n"]
        with mock.patch.object(artifacts, "GLOBAL_PLAN_PROMPT_VERSION", v24):
            split._scope_call(v23_failure_job, profile, facts, units, sizes, tokens,
                              split._client(worker))
        assert calls["n"] > before, "the v23 failure left v24 work to do"
        steps = [
            row for row in storage.list_artifact_job_steps(v23_failure_job.id)
            if row["stage"] == "global_plan"
        ]
        assert sorted(row["status"] for row in steps) == ["failed", "succeeded"], steps


def main() -> None:
    check_one_producer()
    check_schema_bounds()
    check_budget_policy()
    check_window_budgets_in_the_real_path()
    check_single_unit_windows_are_measured_as_leaves()
    check_version_identity()
    check_parent_validators_are_strict()
    check_shared_contract_in_every_planning_contract()
    check_sent_requests_carry_the_shared_contract()
    check_independent_institutions_may_share_one_fact()
    check_verification_route_hangs_on_a_person_or_place()
    check_pure_history_may_stay_a_reference_fact()
    check_card_closure_reaches_materialization()
    check_v23_records_do_not_answer_v24()
    print("PASS: one compact producer, 32k policy, real window budgets, v24 identity, "
          "strict parents, shared v24 semantics on root/window/leaf, per-card closure")


if __name__ == "__main__":
    main()
