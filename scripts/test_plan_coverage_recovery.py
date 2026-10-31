"""Coverage recovery preserves an existing plan and rejects unsafe additions."""
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import artifacts as a, storage  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402


def main():
    expected = {("fact_one", "chapter_one")}
    for chapter in (None, "chapter_one"):
        response = {"decisions": [{"fact_id": "fact_one", "chapter_id": chapter,
                    "destination": "chapter_reference", "reason": "Reference"}]}
        result = a._validate_coverage_decisions(response, expected)
        assert result.decisions[0].chapter_id == "chapter_one"
        assert response["decisions"][0]["chapter_id"] == chapter
    omitted = {"decisions": [{"fact_id": "fact_one", "destination": "chapter_reference", "reason": "Reference"}]}
    assert a._validate_coverage_decisions(omitted, expected).decisions[0].chapter_id == "chapter_one"
    for response, scope, message in [
        (omitted, expected | {("fact_one", "chapter_two")}, "歧义"),
        ({"decisions": [{**omitted["decisions"][0], "chapter_id": "chapter_wrong"}]}, expected, "章节归属错误"),
        ({"decisions": omitted["decisions"] * 2}, expected, "重复"),
        ({"decisions": []}, expected, "遗漏"),
        ({"decisions": [{**omitted["decisions"][0], "fact_id": "unknown"}]}, expected, "未知事实"),
    ]:
        try:
            a._validate_coverage_decisions(response, scope)
        except a.ArtifactGenerationError as error:
            assert message in str(error), str(error)
        else:
            raise AssertionError(message)
    global_response = {"decisions": [{"fact_id": "fact_one", "chapter_id": None,
                        "destination": "global_reference", "reason": "Background"}]}
    assert a._validate_coverage_decisions(global_response, {("fact_one", None)}).decisions[0].chapter_id is None
    facts = [SourceFact(id=f"fact_item{i}", text="Source", kind="event", visibility="explicit",
                        source_refs=[SourceRef(file="fixture://coverage", page=1)]) for i in range(2)]
    bundle = ExampleBundle.model_validate({
        "id": "coverage_test", "name": "Coverage", "profile_ids": ["daggerheart"],
        "facts": [f.model_dump() for f in facts], "cards": [], "plans": [],
        "adventure_chapters": [{"id": "chapter_one", "title": "Chapter", "summary": "Summary",
                                "fact_ids": [f.id for f in facts]}],
        "fact_dispositions": [{"fact_id": f.id, "kind": "chapter", "chapter_ids": ["chapter_one"]} for f in facts],
    })
    raw = {"cards": [{"type": "environment", "title": "Scene", "purpose": "Pressure",
                      "fact_ids": ["fact_item0"]}],
           "brief": {"premise": "Premise", "current_situation": "Situation"},
           "chapter_briefs": [{"chapter_id": "chapter_one", "brief": {
               "premise": "Premise", "current_situation": "Situation"}}]}
    units = [{"fact_ids": [f.id for f in facts]}]
    profile = load_profiles(ROOT / "backend/domain/profiles")["daggerheart"]
    def validator(value):
        return a._validate_global_plan(value, units, facts, profile, bundle, {"chapter_one"})
    class Client:
        calls = 0
        def chat_json(self, messages, **kwargs):
            self.calls += 1
            return {"decisions": [{"fact_id": "fact_item1",
                "destination": "card", "card_index": 0, "reason": "Same scene pressure"}]}
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(storage, "DB_PATH", Path(directory) / "test.db"),
        patch.object(storage, "UPLOAD_DIR", Path(directory) / "uploads"),
    ):
        storage.init_db()
        job, _ = a.create_artifact_job(bundle.id, "daggerheart", model_id="fixture", fake_model=True)
        client = Client()
        try:
            validator(raw)
        except a.PlanCoverageError as error:
            merged = a._complete_plan_coverage(job, "global_plan", 1, raw, error, client, validator)
            again = a._complete_plan_coverage(job, "global_plan", 1, raw, error, client, validator)
        else:
            raise AssertionError("missing facts must fail")
        assert client.calls == 1 and merged == again
        assert raw["cards"][0]["fact_ids"] == ["fact_item0"]
        assert merged["cards"][0]["fact_ids"] == ["fact_item0", "fact_item1"]
        assert validator(merged)["cards"][0]["chapter_ids"] == ["chapter_one"]

        class ResumeClient:
            def __init__(self):
                self.responses = [raw, RuntimeError("offline"), {
                    "decisions": [{"fact_id": "fact_item1", "chapter_id": "chapter_one",
                        "destination": "card", "card_index": 0, "reason": "Same scene pressure"}]
                }]
                self.calls = 0

            def chat_json(self, messages, **kwargs):
                self.calls += 1
                response = self.responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return deepcopy(response)

        resume_client = ResumeClient()
        step_args = {
            "job": job,
            "stage": "global_plan",
            "step_index": 2,
            "input_payload": {"version": "resume-test", "facts": [f.id for f in facts]},
            "input_refs": {"fact_ids": [f.id for f in facts]},
            "messages": [{"role": "user", "content": "plan"}],
            "client": resume_client,
            "max_tokens": 100,
            "validator": validator,
        }
        try:
            a._run_json_step(**step_args)
        except a.PlanCoverageError:
            pass
        else:
            raise AssertionError("failed coverage repair must leave the parent step retryable")
        assert resume_client.calls == 2, "initial plan plus failed repair should be two requests"
        resumed_plan = a._run_json_step(**step_args)
        assert resume_client.calls == 3, "retry must repair the saved plan without requesting it again"
        assert resumed_plan["cards"][0]["fact_ids"] == ["fact_item0", "fact_item1"]
        parent = storage.load_artifact_job_step(
            job.id, "global_plan", 2,
            a._stable_hash({"version": "resume-test", "facts": [f.id for f in facts],
                            "_model_id": job.model_id, "_fake_model": job.fake_model}),
        )
        assert [item["status"] for item in parent["attempts"]] == ["failed", "succeeded"]
        assert a._run_json_step(**step_args) == resumed_plan
        assert resume_client.calls == 3, "successful parent and repair must be reused"
        child = next(item for item in storage.list_artifact_job_steps(job.id)
                     if item["stage"] == "global_plan_coverage" and item["step_index"] == 2)
        assert [item["status"] for item in child["attempts"]] == ["failed", "succeeded"]
        assert raw["cards"][0]["fact_ids"] == ["fact_item0"], "recovery must not mutate the plan"
        from backend.domain import StoryBeat
        bundle.story_beats = [StoryBeat(id="story_beat_one", title="Beat", summary="Situation", chapter_id="chapter_one")]
        linked = deepcopy(merged)
        linked["cards"][0]["story_beat_ids"] = ["story_beat_one"]
        assert validator(linked)["cards"][0]["story_beat_ids"] == ["story_beat_one"]
        for support_type in ("npc", "enemy", "encounter_clock"):
            support = deepcopy(linked["cards"][0])
            support.update(type=support_type, title="Support")
            candidate = deepcopy(linked)
            candidate["cards"].append(support)
            normalized = validator(candidate)
            assert normalized["cards"][1]["story_beat_ids"] == []
            assert normalized["cards"][1]["fact_ids"] == support["fact_ids"]
            assert candidate["cards"][1]["story_beat_ids"] == ["story_beat_one"]
            assert normalized["cards"][0]["story_beat_ids"] == ["story_beat_one"]
            for bad_id, message in (("story_beat_missing", "未知拍"), ("story_beat_other", "跨章节")):
                bundle.story_beats.append(StoryBeat(id="story_beat_other", title="Other", summary="Other", chapter_id="chapter_other"))
                broken = deepcopy(candidate)
                broken["cards"][1]["story_beat_ids"] = [bad_id]
                try:
                    validator(broken)
                except a.ArtifactGenerationError as error:
                    assert message in str(error)
                else:
                    raise AssertionError("invalid support links must not be silently removed")
                finally:
                    bundle.story_beats.pop()
        linked["cards"][0]["story_beat_ids"] = ["story_beat_unknown"]
        try:
            validator(linked)
        except a.ArtifactGenerationError as error:
            assert "未知拍" in str(error)
        else:
            raise AssertionError("unknown beat must be rejected")
        invalid = deepcopy(merged)
        invalid["cards"][0]["chapter_ids"] = ["chapter_wrong"]
        try:
            validator(invalid)
        except a.ArtifactGenerationError:
            pass
        else:
            raise AssertionError("explicit wrong chapters must not be rewritten")
    print("PASS: coverage correction preserves plans, caches decisions, and rejects wrong chapters")


if __name__ == "__main__":
    main()
