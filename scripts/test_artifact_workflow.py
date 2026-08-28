"""Offline R3 checks for fact-bound artifact generation, approval, and scene unlock."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    ExtractionWindow,
    PageSpan,
    PrepJob,
    PrepScope,
    SourceFact,
    SourceRef,
)


def workspace() -> ExampleBundle:
    facts = [
        SourceFact(
            id="fact_artifact_location",
            source_refs=[SourceRef(file="fixture://artifact-source", page=1)],
            evidence_status="source_fact",
            text="The lighthouse keeper's room is abandoned, but a fresh lamp is burning.",
            kind="location",
            visibility="explicit",
        ),
        SourceFact(
            id="fact_artifact_witness",
            source_refs=[SourceRef(file="fixture://artifact-source", page=2)],
            evidence_status="source_fact",
            text="A survivor heard scratching beneath the eastern stairs before the light failed.",
            kind="npc",
            visibility="explicit",
        ),
        SourceFact(
            id="fact_artifact_threat",
            source_refs=[SourceRef(file="fixture://artifact-source", page=3)],
            evidence_status="source_fact",
            text="The creature avoids direct light and circles anyone carrying the brass key.",
            kind="threat",
            visibility="hidden",
        ),
        SourceFact(
            id="fact_artifact_timeline",
            source_refs=[SourceRef(file="fixture://artifact-source", page=4)],
            evidence_status="inference",
            text="If the lamp goes dark again, the creature can reach the keeper's room.",
            kind="timeline",
            visibility="inferred",
        ),
    ]
    return ExampleBundle(
        id="artifact_workflow_test",
        name="Artifact workflow test",
        description="Isolated fake-model R3 workspace.",
        profile_ids=["cthulhu-dark-2e"],
        facts=facts,
        cards=[],
        plans=[],
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "artifact-workflow.db"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-artifact-workflow",
                    "fake": True,
                }
            )
            bundle = workspace()
            storage.save_domain_bundle(
                bundle.id, bundle.model_dump(mode="json", by_alias=True)
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                drafted = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/draft"
                )
                assert drafted.status_code == 202, drafted.text
                job = drafted.json()["job"]
                assert job["status"] in {"queued", "running", "completed"}
                cards = []
                for _ in range(50):
                    job_response = await client.get(
                        f"/api/domain/examples/{bundle.id}/cards/draft-jobs/{job['id']}"
                    )
                    assert job_response.status_code == 200, job_response.text
                    job = job_response.json()["job"]
                    if job["status"] == "completed":
                        break
                    if job["status"] == "failed":
                        raise AssertionError(job)
                    await asyncio.sleep(0.02)
                assert job["status"] == "completed", job
                reloaded = await client.get(
                    "/api/domain/workbench", params={"example": bundle.id}
                )
                assert reloaded.status_code == 200, reloaded.text
                cards = reloaded.json()["bundle"]["cards"]
                assert {card["type"] for card in cards} == {
                    "scene", "npc", "threat", "clock"
                }
                assert all(card["edit_state"] == "generated" for card in cards)
                assert all(card["generation"]["model_id"] == "fake-artifact-workflow" for card in cards)
                assert all(card["field_sources"] for card in cards)
                assert all(
                    set(source_ids).issubset(card["fact_ids"])
                    for card in cards
                    for source_ids in card["field_sources"].values()
                )

                duplicate = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/draft"
                )
                assert duplicate.status_code == 409, duplicate.text

                blocked_plan = await client.post(
                    f"/api/domain/examples/{bundle.id}/plans/draft"
                )
                assert blocked_plan.status_code == 422, blocked_plan.text
                assert "已批准" in blocked_plan.json()["detail"]

                card_ids = [card["id"] for card in cards]
                approved = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/review",
                    json={"card_ids": card_ids, "action": "approve"},
                )
                assert approved.status_code == 200, approved.text
                assert all(
                    card["edit_state"] == "approved"
                    for card in approved.json()["cards"]
                )

                plan_response = await client.post(
                    f"/api/domain/examples/{bundle.id}/plans/draft"
                )
                assert plan_response.status_code == 200, plan_response.text
                plan = plan_response.json()["plan"]
                assert plan["title"].endswith("运行场景")
                assert set(plan["card_ids"]) == set(card_ids)

                reopen = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/review",
                    json={"card_ids": [card_ids[0]], "action": "reopen"},
                )
                assert reopen.status_code == 409, reopen.text
                deleted = await client.delete(
                    f"/api/domain/examples/{bundle.id}/cards/{card_ids[0]}"
                )
                assert deleted.status_code == 409, deleted.text

                reloaded = await client.get(
                    "/api/domain/workbench", params={"example": bundle.id}
                )
                assert reloaded.status_code == 200, reloaded.text
                saved_bundle = reloaded.json()["bundle"]
                assert len(saved_bundle["cards"]) == 4
                assert len(saved_bundle["plans"]) == 1

                task_ids = []
                for index in range(2):
                    task_response = await client.post(
                        "/api/domain/shadow/tasks",
                        json={
                            "idempotency_key": f"artifact-prep-batch-{index}",
                            "source_file": "fixture://artifact-source",
                            "source_version": "v1",
                            "source_pages": [index + 1],
                            "profile_id": "cthulhu-dark-2e",
                            "model_id": "fake-artifact-workflow",
                            "prompt_version": "test",
                            "schema_version": "test",
                            "input_excerpt": "fixture",
                        },
                    )
                    assert task_response.status_code == 200, task_response.text
                    task_id = task_response.json()["task"]["id"]
                    task_ids.append(task_id)
                    run_response = await client.post(
                        f"/api/domain/shadow/tasks/{task_id}/runs",
                        json={
                            "raw_response": '{"candidates": [{"text": "batch candidate", "kind": "event", "source_refs": [{"file": "fixture://artifact-source", "page": %d}]}]}'
                            % (index + 1)
                        },
                    )
                    assert run_response.status_code == 200, run_response.text
                prep_job = PrepJob(
                    id="prep_job_artifact_batch",
                    status="completed",
                    scope=PrepScope(
                        source_file="fixture://artifact-source",
                        source_version="v1",
                        page_spans=[PageSpan(start=1, end=2)],
                        profile_id="cthulhu-dark-2e",
                        objective="batch review",
                    ),
                    model_id="fake-artifact-workflow",
                    prompt_version="test",
                    schema_version="test",
                    workspace_id=bundle.id,
                    windows=[
                        ExtractionWindow(
                            id=f"prep_window_artifact_{index}",
                            page_span=PageSpan(start=index + 1, end=index + 1),
                            core_span=PageSpan(start=index + 1, end=index + 1),
                            shadow_task_id=task_ids[index],
                            status="succeeded",
                            candidate_count=1,
                            input_chars=10,
                        )
                        for index in range(2)
                    ],
                    candidate_count=2,
                    created_at=storage.now(),
                    updated_at=storage.now(),
                )
                storage.create_prep_job(prep_job.model_dump(mode="json"))
                prep_candidates = await client.get(
                    f"/api/domain/prep/jobs/{prep_job.id}/candidates"
                )
                assert prep_candidates.status_code == 200, prep_candidates.text
                candidate_ids = [item["id"] for item in prep_candidates.json()["candidates"]]
                assert len(candidate_ids) == 2
                batch = await client.post(
                    f"/api/domain/prep/jobs/{prep_job.id}/candidates/review",
                    json={"candidate_ids": candidate_ids, "review_state": "accepted"},
                )
                assert batch.status_code == 200, batch.text
                assert all(item["review_state"] == "accepted" for item in batch.json()["candidates"])

                failure_bundle = workspace().model_copy(update={"id": "artifact_failure_test"})
                storage.save_domain_bundle(
                    failure_bundle.id,
                    failure_bundle.model_dump(mode="json", by_alias=True),
                )
                original_generate = artifacts._generate_direct_step
                artifacts._generate_direct_step = lambda *args, **kwargs: (_ for _ in ()).throw(
                    artifacts.ArtifactGenerationError("forced artifact failure")
                )
                try:
                    failed_request = await client.post(
                        f"/api/domain/examples/{failure_bundle.id}/cards/draft"
                    )
                    assert failed_request.status_code == 202, failed_request.text
                    failed_job_id = failed_request.json()["job"]["id"]
                    failed_status = await client.get(
                        f"/api/domain/examples/{failure_bundle.id}/cards/draft-jobs/{failed_job_id}"
                    )
                    assert failed_status.status_code == 200, failed_status.text
                    assert failed_status.json()["job"]["status"] == "failed"
                    assert "forced artifact failure" in failed_status.json()["job"]["error"]
                finally:
                    artifacts._generate_direct_step = original_generate
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("artifact workflow checks passed")


if __name__ == "__main__":
    asyncio.run(main())
