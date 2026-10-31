"""Offline prompt contracts for source semantics across artifact generation stages."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402
from backend.domain.prep import FactDisposition, PageSpan  # noqa: E402


def payload(messages: list[dict[str, str]], key: str):
    prefix = f"{key}="
    return json.loads(next(line[len(prefix):] for line in messages[1]["content"].splitlines()
                           if line.startswith(prefix)))


profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
fact = SourceFact(
    id="fact_semantic_contract",
    text=("The guardian can seal a doorway when disturbed; its source HP is 12. "
          "In the example session, the party defeated it and escaped. "
          "The rising flood threatens the vault; the archive describes an older flood."),
    kind="threat",
    visibility="explicit",
    source_refs=[SourceRef(file="fixture://artifact-semantics", page=3)],
)
facts = [fact.model_dump(mode="json")]
units = [{
    "id": "local_unit_1", "kind": "threat", "title": "Guardian and flood",
    "summary": fact.text, "fact_ids": [fact.id], "entity_keys": ["guardian"],
    "relationship_hints": [], "open_questions": [],
}]
structure = {"chapters": [{"id": "chapter_1", "title": "Vault"}], "beats": []}

# Prompt construction must not consult a live model or persist anything.
with patch.object(artifacts, "make_client", side_effect=AssertionError("upstream call")), \
     patch.object(artifacts.storage, "save_artifact_job_step",
                  side_effect=AssertionError("database write")):
    for profile_id in ("daggerheart", "cthulhu-dark-2e"):
        profile = profiles[profile_id]
        bundle = ExampleBundle(id="semantic_contract", name="Semantic contract",
                               profile_ids=[profile.id], facts=[fact], cards=[], plans=[])
        local = artifacts._local_digest_messages(profile, facts, batch_index=1, batch_count=1)
        scoped = artifacts._global_plan_messages(
            profile, units, {fact.id: len(fact.text)}, {fact.id: 80}, structure)
        unscoped = artifacts._global_plan_messages(
            profile, units, {fact.id: len(fact.text)}, {fact.id: 80})
        direct = artifacts._prompt_messages(bundle, profile, facts)
        materialized = []
        for definition in profile.card_definitions:
            plan = {"type": definition.type, "title": "Guardian and flood", "fact_ids": [fact.id]}
            messages = artifacts._materialization_messages(profile, plan, facts)
            materialized.append(messages)
            assert payload(messages, "ORIGINAL_FACTS_JSON") == facts
            assert payload(messages, "CARD_PLAN_JSON") == plan
            assert payload(messages, "PROMPT_VERSION_JSON") == "prep-artifact-draft-v13"
            system = messages[0]["content"]
            assert "source_statistics, when supported, is a concise string quoting only supplied source information" in system
            assert "Every factual field must cite one or more fact_ids" in system
            assert "Every required field listed below MUST be present and non-empty" in system
            assert "Never use that exception for factual claims" in system
            assert "assumed_state is one concise declarative sentence" in system
            assert "table_questions holds two to four short prompts" in system
            assert "usable_features holds two to five named features" in system
            assert "Begin environment_type with one scene rhythm" in system

        for messages in [local, scoped, unscoped, direct, *materialized]:
            system = messages[0]["content"]
            for requirement in (
                "single evolving pressure", "advancement triggers", "observable escalation stages",
                "endpoint consequence", "not a room itinerary, clue sequence, historical",
                "without a supported evolving pressure", "factual triggers and consequences require",
                "Distinguish source play reports and examples from default current state",
                "Do not promote reported player choices", "Keep example outcomes conditional",
                "state what the material assumes in assumed_state",
                "never turn them into review questions", "Preserve reusable actionable capabilities",
                "triggers, limits, costs, and counterplay", "A fact ID alone is not coverage",
                "reflect it in appropriate declared", "do not invent or convert statistics",
                "Do not set current_stage from a play report's completed outcome",
            ):
                assert requirement in system, (profile_id, messages[1]["content"].splitlines()[0], requirement)
        assert payload(local, "LOCAL_FACTS_JSON") == facts
        assert payload(direct, "REVIEWED_FACTS_JSON") == facts
        assert payload(local, "PROMPT_VERSION_JSON") == "prep-artifact-draft-v13"
        for messages in (scoped, unscoped):
            assert payload(messages, "PROMPT_VERSION_JSON") == "prep-artifact-global-plan-v18"
        # Problems 2 and 4: the planner must declare one situation per
        # environment card and mark how the material relates to the opening.
        assert "situation, and case_scope" in scoped[0]["content"]
        if profile.runtime_navigation_mode == "environment":
            assert "exactly one playable situation" in scoped[0]["content"]
            assert "Set case_scope on every card" in scoped[0]["content"]
            # Runtime groups must stay canonical, so one situation keeps one key and the
            # planning scope never leaks into a name. The key must also be ASCII: a Chinese
            # name cannot be slugged and collapses to a placeholder identifier.
            assert "reuse one canonical name across chapters" in scoped[0]["content"]
            assert "never a campaign-wide theme" in scoped[0]["content"]
            assert "short lowercase ASCII key joined by underscores" in scoped[0]["content"]
        assert payload(scoped, "REVIEWED_STRUCTURE_JSON") == structure
        assert "THIS request generates ONLY REVIEWED_STRUCTURE_JSON.chapters" in scoped[0]["content"]

assert artifacts.PROMPT_VERSION == "prep-artifact-draft-v13"
assert artifacts.GLOBAL_PLAN_PROMPT_VERSION == "prep-artifact-global-plan-v18"

handouts = [fact.model_copy(update={"id": name, "kind": "handout"}) for name in (
    "letter", "asset", "unassigned", "reference", "candidate", "outside_scope",
)]
handouts[-2] = handouts[-2].model_copy(update={"evidence_status": "model_candidate"})
handouts[-1] = handouts[-1].model_copy(update={
    "source": SourceRef(file="fixture://artifact-semantics", page=9),
    "source_refs": [SourceRef(file="fixture://artifact-semantics", page=9)],
})
bundle = ExampleBundle(
    id="handout_contract", name="Handout contract", profile_ids=["daggerheart"],
    facts=[fact, *handouts], cards=[], plans=[],
    fact_dispositions=[
        FactDisposition(fact_id=name, kind="chapter", chapter_ids=["chapter_1"])
        for name in ("letter", "candidate", "outside_scope")
    ] + [FactDisposition(fact_id="asset", kind="display_material"),
         FactDisposition(fact_id="reference", kind="global_reference")],
)
with patch.object(artifacts.prep, "find_prep_job_by_workspace", return_value=None):
    assert {item.id for item in artifacts._facts_for_workspace(bundle, bundle.id)} == {
        fact.id, "letter", "outside_scope",
    }
    assert [item.id for item in artifacts._facts_for_workspace(
        bundle.model_copy(update={"fact_dispositions": []}), bundle.id)] == [fact.id]
scope = SimpleNamespace(source_file="fixture://artifact-semantics", page_spans=[PageSpan(start=3, end=3)])
with patch.object(artifacts.prep, "find_prep_job_by_workspace",
                  return_value=SimpleNamespace(scope=scope)):
    assert {item.id for item in artifacts._facts_for_workspace(bundle, bundle.id)} == {fact.id, "letter"}
assert "its handout kind does not make that content display-only" in scoped[0]["content"]
print("PASS: artifact prompts preserve pressure clocks, source modality, capabilities, and citations")
print("PASS: chapter handouts survive filtering while default exclusion and scope remain intact")
