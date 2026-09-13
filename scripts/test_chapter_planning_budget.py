"""Chapter-owned digest inputs and lossless planning transport under pressure."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a, storage  # noqa: E402
from backend.app.planning_transport import PlanningTransport  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402


def main():
    facts = [SourceFact(
        id=f"fact_promoted_source_{index:016d}", text=f"Fact {index}", kind="event",
        visibility="explicit", source_refs=[SourceRef(file="fixture://budget", page=1)],
    ) for index in range(32)]
    ids = [fact.id for fact in facts]
    bundle = ExampleBundle.model_validate({
        "id": "chapter_budget", "name": "Budget", "profile_ids": ["daggerheart"],
        "facts": [fact.model_dump() for fact in facts], "cards": [], "plans": [],
        "adventure_chapters": [
            {"id": "chapter_first", "title": "First", "summary": "First", "fact_ids": ids[:16]},
            {"id": "chapter_second", "title": "Second", "summary": "Second", "fact_ids": ids[15:31]},
        ],
    })
    batches = a._artifact_batches(bundle, facts)
    assert [{fact.id for fact in batch} for batch in batches] == [set(ids[:16]), set(ids[15:31])]
    assert all(ids[-1] not in {fact.id for fact in batch} for batch in batches)
    units = [{"id": f"unit_{index}", "kind": "scene", "title": f"Scene {index}",
              "summary": "Detailed narrative context. " * 45, "fact_ids": [fact.id],
              "entity_keys": [], "relationship_hints": [], "open_questions": []}
             for index, fact in enumerate(facts)]
    context = {"chapters": [{"id": "chapter_first", "title": "First", "summary": "First"}],
               "adventure_index": [], "threads": [], "beats": [],
               "fact_dispositions": [{"fact_id": fid, "kind": "chapter", "chapter_ids": ["chapter_first"]}
                                     for fid in ids]}
    sizes = {fid: 200 for fid in ids}
    profile = load_profiles(ROOT / "backend/domain/profiles")["daggerheart"]

    class Client:
        calls = 0
        requests = []
        def chat_json(self, messages, **kwargs):
            self.calls += 1
            self.requests.append(messages)
            assert 'Return exactly one JSON object' in messages[0]["content"]
            assert 'English field names title and summary' in messages[0]["content"]
            assert "Preserve whether" in messages[0]["content"]
            assert "single evolving pressure" in messages[0]["content"]
            assert len(json.dumps(messages, ensure_ascii=False)) <= 24_000
            return {"title": "Situations", "summary": "Keep the separate situations and their leads accessible."}

    with tempfile.TemporaryDirectory() as directory, patch.object(storage, "DB_PATH", Path(directory) / "test.db"):
        storage.init_db()
        job, _ = a.create_artifact_job(bundle.id, "daggerheart", model_id="scripted", fake_model=True)
        client = Client()
        original = json.dumps(units)
        with patch.object(a, "MAX_FACT_INPUT_CHARS", 24_000):
            compact, messages, transport = a._bounded_plan_input(job, 1, profile, units, sizes, sizes, context, client)
            assert len(compact) < len(units)
            # A fixed 8:1 collapse folded these 32 units into 4 entries and then
            # kept folding, which is exactly the large-chapter over-compression
            # this budget-derived grouping replaced.  Measured value is 8.
            assert len(compact) >= 5, len(compact)
            assert {fid for unit in compact for fid in unit["fact_ids"]} == set(ids)
            assert len(json.dumps(transport.messages(messages), ensure_ascii=False)) <= 24_000
            assert client.calls > 0
            count = client.calls
            repeated, _, _ = a._bounded_plan_input(job, 1, profile, units, sizes, sizes, context, client)
            assert repeated == compact and client.calls == count
        assert json.dumps(units) == original
        with patch.object(a, "MAX_FACT_INPUT_CHARS", 100):
            try:
                a._bounded_plan_input(job, 1, profile, units, sizes, sizes, context, client)
            except a.ArtifactGenerationError as error:
                assert "固定章节上下文" in str(error)
            else:
                raise AssertionError("fixed context must fail before summarization")
            assert client.calls == count

    class Echo:
        def chat_json(self, messages, **kwargs):
            body = json.loads(messages[0]["content"].split("=", 1)[1])
            assert ids[0] not in body["fact_ids"]
            return body

    transport = PlanningTransport(Echo(), ids)
    payload = {"fact_ids": ids, "field_sources": {"summary": ids[:2]},
               "budgets": {ids[0]: 200}, "title": "f0", "summary": ids[0]}
    assert transport.chat_json([{"role": "user", "content": "TEST_JSON=" + json.dumps(payload)}]) == payload
    structure = {"chapters": [{"id": "chapter_one"}],
                 "adventure_index": [{"id": "chapter_two"}],
                 "threads": [{"id": "thread_one"}], "beats": [{"id": "beat_one"}]}
    transport = PlanningTransport(Echo(), ids, structure)
    payload.update(chapter_ids=["chapter_one", "chapter_two"], story_beat_ids=["beat_one"],
                   thread_ids=["thread_one"], chapter_roles={"chapter_one": "develop"},
                   thread_developments={"thread_one": {"development": "t0", "carry_forward": "chapter_one"}})
    encoded = transport.messages([{"role": "user", "content": "TEST_JSON=" + json.dumps(payload)}])
    body = json.loads(encoded[0]["content"].split("=", 1)[1])
    assert body["chapter_ids"] == ["chapter_0", "chapter_1"]
    assert body["story_beat_ids"] == ["story_beat_0"]
    assert body["thread_developments"]["thread_0"]["development"] == "t0"
    assert transport.chat_json([{"role": "user", "content": "TEST_JSON=" + json.dumps(payload)}]) == payload
    print("PASS: chapter ownership, reversible IDs, budgeted index reduction, and cached retry")


if __name__ == "__main__":
    main()
