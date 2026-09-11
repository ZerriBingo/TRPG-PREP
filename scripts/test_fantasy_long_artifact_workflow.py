"""Long-form fantasy builds keep every approved chapter inside one artifact job."""
from __future__ import annotations

import sys
import json
from copy import deepcopy
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage, structure  # noqa: E402
from backend.domain import ExampleBundle, FactDisposition, SourceFact, SourceRef  # noqa: E402


def main() -> None:
    for response in (artifacts.DraftResponsePayload, artifacts.GlobalPlanResponsePayload):
        assert response.model_json_schema()["properties"]["cards"]["maxItems"] == 120
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "fantasy-long-artifact.db"
        try:
            storage.init_db()
            facts = [
                SourceFact(
                    id=f"fact_long_{index:03d}",
                    text=f"长篇冒险事实 {index}，用于验证完整章节生成。",
                    kind="event",
                    visibility="explicit",
                    source_refs=[
                        SourceRef(file="fixture://fantasy-long", page=index // 5 + 1)
                    ],
                )
                for index in range(201)
            ]
            bundle = ExampleBundle(
                id="fantasy_long_artifact",
                name="长篇奇幻产物工作流",
                profile_ids=["daggerheart"],
                facts=facts,
                cards=[],
                plans=[],
            )
            storage.save_domain_bundle(
                bundle.id, bundle.model_dump(mode="json", by_alias=True)
            )

            structure_job, created = structure.create_structure_job(
                bundle.id, "daggerheart", model_id="fake-long", fake_model=True
            )
            assert created
            structure.execute_structure_job(structure_job.id)
            assert structure.get_structure_job(structure_job.id).status == "completed"
            structured = structure.approve_structure(bundle.id)
            structured.facts.append(SourceFact(
                id="fact_global_reference", text="General setting reference", kind="resource",
                visibility="explicit", source_refs=[SourceRef(file="fixture://fantasy-long", page=1)],
            ))
            structured.fact_dispositions.append(FactDisposition(
                fact_id="fact_global_reference", kind="global_reference",
            ))
            storage.save_domain_bundle(bundle.id, structured.model_dump(mode="json"))

            artifact_job, created = artifacts.create_artifact_job(
                bundle.id, "daggerheart", model_id="fake-long", fake_model=True
            )
            assert created
            original_client = artifacts.make_client(model_id="fake-long", force_fake=True)
            planning_requests = []
            local_calls = []
            stop_before_planning = True

            class InspectingClient:
                def chat_json(self, messages, **kwargs):
                    content = messages[1]["content"]
                    if "[TASK:prep:artifact_local_digest]" in content:
                        local_input = {
                            key: json.loads(value) for line in content.splitlines() if "_JSON=" in line
                            for key, value in [line.split("=", 1)]
                        }
                        chapter = next(ch for ch in structured.adventure_chapters
                                       if ch.id == local_input["CHAPTER_CONTEXT_JSON"]["id"])
                        assert {f["id"] for f in local_input["LOCAL_FACTS_JSON"]} <= set(chapter.fact_ids)
                        assert "fact_global_reference" not in content
                        local_calls.append(content)
                    if "[TASK:prep:artifact_global_plan]" in content:
                        if stop_before_planning:
                            raise RuntimeError("fixture: interrupt before planning")
                        values = {
                            key: json.loads(value)
                            for line in content.splitlines() if "_JSON=" in line
                            for key, value in [line.split("=", 1)]
                        }
                        context = values["REVIEWED_STRUCTURE_JSON"]
                        for beat_context in context["beats"]:
                            assert set(beat_context["thread_developments"]) == set(beat_context["thread_ids"])
                            assert beat_context["interplay"]
                        owned = {item["fact_id"] for item in context["fact_dispositions"]}
                        assert set(values["FACT_INPUT_CHARS_JSON"]) == owned
                        assert set(values["FACT_INPUT_TOKENS_JSON"]) == owned
                        schema = values["RESPONSE_SCHEMA_JSON"]
                        assert schema["$defs"]["PlannedCardPayload"]["additionalProperties"] is False
                        assert schema["$defs"]["ChapterBriefPayload"]["required"] == ["chapter_id", "brief"]
                        assert "Generate all approved chapters" not in messages[0]["content"]
                        assert "environment_type=traversal" not in messages[0]["content"]
                        assert len(context["chapters"]) == 1
                        planning_requests.append(context["chapters"][0]["id"])
                        output = original_client.chat_json(messages, **kwargs)
                        chapter_fact = next(item["fact_id"] for item in context["fact_dispositions"] if item["kind"] == "chapter")
                        output["chapter_briefs"][0]["reference_fact_ids"] = [chapter_fact]
                        output["brief"] = None
                        # Each chapter response fits its budget; their combined result exceeds 120.
                        template = next(card for card in output["cards"] if card["type"] == "environment")
                        while len(output["cards"]) < 65:
                            card = deepcopy(template)
                            card["title"] = f"Fixture environment {planning_requests[-1]} {len(output['cards'])}"
                            output["cards"].append(card)
                        artifacts.GlobalPlanResponsePayload.model_validate(output)
                        return output
                    return original_client.chat_json(messages, **kwargs)

            with patch.object(artifacts, "make_client", return_value=InspectingClient()):
                artifacts.execute_artifact_job(
                    artifact_job.id, workspace_id=bundle.id, profile_id="daggerheart"
                )
                assert artifacts.get_artifact_job(artifact_job.id).status == "failed"
                completed_local_calls = len(local_calls)
                assert completed_local_calls > 0
                # Stored provenance is omitted from planning requests, regardless of its size.
                for step in storage.list_artifact_job_steps(artifact_job.id):
                    if step["stage"] == "local_digest":
                        for unit in step["output"]["units"]:
                            unit["source_refs"] = [{"file": "fixture://" + "x" * 120_001, "page": 1}]
                        storage.save_artifact_job_step(step)
                stop_before_planning = False
                artifacts.retry_artifact_job(
                    artifact_job.id, model_id="fake-long", fake_model=True
                )
                with patch.object(artifacts, "MAX_FACT_INPUT_CHARS", 1):
                    artifacts.execute_artifact_job(
                        artifact_job.id, workspace_id=bundle.id, profile_id="daggerheart"
                    )
                oversized = artifacts.get_artifact_job(artifact_job.id)
                assert oversized.status == "failed"
                assert "规划请求过大" in oversized.error
                assert not planning_requests, "over-budget requests must not reach the model"
                artifacts.retry_artifact_job(
                    artifact_job.id, model_id="fake-long", fake_model=True
                )
                artifacts.execute_artifact_job(
                    artifact_job.id, workspace_id=bundle.id, profile_id="daggerheart"
                )
                assert len(local_calls) == completed_local_calls, "retry must reuse successful digests"
            finished = artifacts.get_artifact_job(artifact_job.id)
            assert finished.status == "completed", finished.error
            assert finished.planning_scope_count == 2
            assert finished.completed_planning_scopes == 2
            assert len(set(planning_requests)) == 2

            saved = storage.load_domain_bundle(bundle.id)
            assert saved
            generated = ExampleBundle.model_validate(saved[0])
            chapter_ids = {chapter.id for chapter in generated.adventure_chapters}
            assert len(chapter_ids) == 2
            assert all(chapter.overview for chapter in generated.adventure_chapters)
            assert generated.runtime_brief is None
            assert any(f.id == "fact_global_reference" for f in generated.facts)
            assert any(d.fact_id == "fact_global_reference" and d.kind == "global_reference"
                       for d in generated.fact_dispositions)
            assert all("fact_global_reference" not in card.fact_ids for card in generated.cards)
            assert all(chapter.reference_fact_ids for chapter in generated.adventure_chapters)
            assert generated.cards
            assert len(generated.cards) > 120, "chapter totals must not inherit a single-response cap"
            assert all(card.chapter_ids for card in generated.cards)
            assert set().union(*(set(card.chapter_ids) for card in generated.cards)) == chapter_ids
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: one long-form build completes every approved chapter")


if __name__ == "__main__":
    main()
