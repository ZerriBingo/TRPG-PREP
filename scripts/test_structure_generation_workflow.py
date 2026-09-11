"""Exercise structure generation with model response shapes seen in real runs."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage, structure  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef  # noqa: E402


SKELETON = {
    "chapters": [
        {"key": "chapter1_opening", "title": "Opening", "summary": "An invitation."},
        {"key": "chapter2_reveal", "title": "Revelation", "summary": "A secret emerges."},
    ],
    "threads": [
        {
            "key": "secret",
            "title": "The secret",
            "kind": "truth_chain",
            "chapter_roles": {"chapter1_opening": "introduce", "chapter2_reveal": "resolve"},
        }
    ],
    "beats": [{
        "key": "uncertain_offer", "title": "An offer under suspicion",
        "summary": "An invitation can expose the first inconsistency.",
        "chapter_key": "chapter1_opening", "thread_keys": ["secret"],
        "fact_ids": ["fact_invitation", "fact_secret"],
        "thread_developments": {"secret": {
            "development": "The invitation may reveal a contradiction in the host's story.",
            "carry_forward": "The host's identity remains unresolved for later investigation.",
        }},
        "interplay": "Accepting or questioning the offer changes access to the secret, without fixing a route.",
    }],
}
ASSIGNMENTS = {
    "fact_invitation": {"kind": "chapter", "chapter_keys": ["chapter1_opening"]},
    "fact_secret": {
        "kind": "chapter",
        "chapter_keys": ["chapter1_opening", "chapter2_reveal"],
        "thread_keys": ["secret"],
    },
    "fact_context": {"kind": "global_reference"},
}


class ResponseClient:
    def __init__(self, assignments, skeleton):
        self.assignments = assignments
        self.skeleton = skeleton
        self.assignment_calls = 0

    def chat_json(self, messages, **kwargs):
        task = messages[1]["content"]
        schema = json.loads(next(
            line.removeprefix("RESPONSE_SCHEMA_JSON=")
            for line in task.splitlines() if line.startswith("RESPONSE_SCHEMA_JSON=")
        ))
        if "[TASK:prep:structure_local]" in task:
            return {"observations": [{
                "title": "A secret invitation", "summary": "The invitation leads to a secret.",
                "fact_ids": list(ASSIGNMENTS),
            }]}
        if "[TASK:prep:structure_consolidate]" in task:
            return deepcopy(self.skeleton)
        assert "[TASK:prep:structure_assign]" in task
        assert schema["properties"]["assignments"]["type"] == "array"
        fields = schema["$defs"]["FactAssignment"]["properties"]
        assert set(fields["fact_id"]["enum"]) == set(ASSIGNMENTS)
        assert fields["chapter_keys"]["items"]["enum"] == [
            item["key"] for item in self.skeleton["chapters"]
        ]
        self.assignment_calls += 1
        return deepcopy(self.assignments)


def run_case(raw, *, valid=True, skeleton=None):
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(storage, "DB_PATH", Path(directory) / "structure.db"):
            storage.init_db()
            facts = [SourceFact(
                id=fact_id, text=fact_id, kind="event", visibility="explicit",
                source_refs=[SourceRef(file="fixture://structure", page=1)],
            ) for fact_id in ASSIGNMENTS]
            bundle = ExampleBundle(
                id="structure_response_workflow", name="Structure response workflow",
                profile_ids=["daggerheart"], facts=facts, cards=[], plans=[],
            )
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json"))
            job, created = structure.create_structure_job(
                bundle.id, "daggerheart", model_id="scripted", fake_model=True,
            )
            assert created
            client = ResponseClient(raw, skeleton or SKELETON)
            with patch.object(structure, "make_client", return_value=client):
                structure.execute_structure_job(job.id)
            result = structure.get_structure_job(job.id)
            saved = storage.load_domain_bundle(bundle.id)
            assert saved is not None
            output = ExampleBundle.model_validate(saved[0])
            assert output.facts == facts
            if valid:
                assert result.status == "completed", result.error
                assert client.assignment_calls == 1, "shape-only variations need no model retry"
                assert len(output.adventure_chapters) == 2
                assert {item.fact_id for item in output.fact_dispositions} == set(ASSIGNMENTS)
                secret = next(item for item in output.fact_dispositions if item.fact_id == "fact_secret")
                assert len(secret.chapter_ids) == 2
                assert len(secret.thread_ids) == 1
                context = next(item for item in output.fact_dispositions if item.fact_id == "fact_context")
                assert context.kind == "global_reference" and not context.chapter_ids
                beat = output.story_beats[0]
                thread = output.story_threads[0]
                assert beat.thread_ids == [thread.id]
                assert beat.thread_developments[thread.id].development == SKELETON["beats"][0]["thread_developments"]["secret"]["development"]
                assert beat.thread_developments[thread.id].carry_forward.endswith("later investigation.")
                assert beat.interplay == SKELETON["beats"][0]["interplay"]
            else:
                assert result.status == "failed", result
                assert not output.adventure_chapters and not output.fact_dispositions


def main():
    run_case({"assignments": deepcopy(ASSIGNMENTS)})
    for defect in ("missing_progress", "unknown_thread", "blank_change", "missing_interplay"):
        broken = deepcopy(SKELETON)
        beat = broken["beats"][0]
        if defect == "missing_progress":
            beat["thread_developments"] = {}
        elif defect == "unknown_thread":
            beat["thread_developments"]["unknown"] = beat["thread_developments"].pop("secret")
        elif defect == "blank_change":
            beat["thread_developments"]["secret"]["development"] = "   "
        else:
            del beat["interplay"]
        run_case({"assignments": deepcopy(ASSIGNMENTS)}, valid=False, skeleton=broken)
    run_case(deepcopy(ASSIGNMENTS))
    run_case({"assignments": [dict(value, fact_id=key) for key, value in ASSIGNMENTS.items()]})
    aliases = deepcopy(ASSIGNMENTS)
    aliases["fact_invitation"]["chapter_keys"] = ["chapter1"]
    aliases["fact_secret"]["chapter_keys"] = ["chapter1", "chapter2", "chapter2_reveal"]
    run_case({"assignments": aliases})

    ambiguous = deepcopy(SKELETON)
    ambiguous["chapters"].append({
        "key": "chapter1_alternative", "title": "Alternative opening", "summary": "A different opening.",
    })
    run_case({"assignments": aliases}, valid=False, skeleton=ambiguous)
    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_invitation"]["chapter_keys"] = ["chapter"]
    run_case({"assignments": invalid}, valid=False)
    invalid["fact_invitation"]["chapter_keys"] = ["chapter1_open"]
    run_case({"assignments": invalid}, valid=False)
    invalid["fact_invitation"]["chapter_keys"] = ["chapter_unknown"]
    run_case({"assignments": invalid}, valid=False)

    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_secret"]["thread_keys"] = ["unknown"]
    run_case({"assignments": invalid}, valid=False)
    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_secret"]["fact_id"] = "fact_invitation"
    run_case({"assignments": invalid}, valid=False)
    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_secret"]["invented_field"] = "must not be discarded"
    run_case({"assignments": invalid}, valid=False)
    run_case({"assignments": ASSIGNMENTS, "invented_field": True}, valid=False)
    run_case({"assignments": {"fact_unknown": {"kind": "global_reference"}}}, valid=False)
    run_case({"assignments": {"fact_invitation": ASSIGNMENTS["fact_invitation"]}}, valid=False)
    duplicate = [dict(value, fact_id=key) for key, value in ASSIGNMENTS.items()]
    run_case({"assignments": duplicate + [duplicate[0]]}, valid=False)
    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_context"] = {"kind": "deferred"}
    run_case({"assignments": invalid}, valid=False)
    invalid = deepcopy(ASSIGNMENTS)
    invalid["fact_invitation"]["chapter_keys"] = []
    run_case({"assignments": invalid}, valid=False)
    print("PASS: structure generation accepts equivalent assignment shapes without retries")


if __name__ == "__main__":
    main()
