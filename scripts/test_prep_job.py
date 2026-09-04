"""Offline R1 checks for cross-page prep jobs and candidate generation."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.app.main import app  # noqa: E402


def make_source_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 9):
            page = document.new_page()
            page.insert_text(
                (72, 72),
                (
                    f"Page {page_number} source text. "
                    f"Scene detail {page_number} continues into nearby pages. "
                    "A named witness, location, clue, and consequence may depend on context."
                ),
            )
        document.save(path)
    finally:
        document.close()


async def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    data_root = ROOT / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_root) as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "cross-page-source.pdf"
        make_source_pdf(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "prep-job.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-prep-r1",
                    "fake": True,
                }
            )
            assert storage.get_config()["fake"] is True

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                uploaded = await client.post(
                    "/api/domain/source-files",
                    files={
                        "file": (
                            "uploaded-cross-page.pdf",
                            source_path.read_bytes(),
                            "application/pdf",
                        )
                    },
                )
                assert uploaded.status_code == 200, uploaded.text
                upload_payload = uploaded.json()
                assert upload_payload["page_count"] == 8
                relative_source = upload_payload["file"]
                source_files = await client.get("/api/domain/source-files")
                assert source_files.status_code == 200, source_files.text
                assert relative_source in source_files.json()["uploads"]

                invalid = await client.post(
                    "/api/domain/prep/jobs",
                    json={
                        "source_file": relative_source,
                        "page_range": "6-2",
                        "profile_id": "cthulhu-dark-2e",
                    },
                )
                assert invalid.status_code == 422, invalid.text

                outside = await client.post(
                    "/api/domain/prep/jobs",
                    json={
                        "source_file": relative_source,
                        "page_range": "8-9",
                        "profile_id": "cthulhu-dark-2e",
                    },
                )
                assert outside.status_code == 422, outside.text

                created = await client.post(
                    "/api/domain/prep/jobs",
                    json={
                        "source_file": relative_source,
                        "page_range": "2-6, 8",
                        "profile_id": "cthulhu-dark-2e",
                    },
                )
                assert created.status_code == 200, created.text
                job = created.json()["job"]
                job_id = job["id"]
                assert job["fake_model"] is True
                assert job["scope"]["page_spans"] == [
                    {"start": 2, "end": 6, "label": None},
                    {"start": 8, "end": 8, "label": None},
                ]
                assert [
                    (window["page_span"]["start"], window["page_span"]["end"])
                    for window in job["windows"]
                ] == [(2, 5), (4, 6), (8, 8)]
                assert [
                    (window["core_span"]["start"], window["core_span"]["end"])
                    for window in job["windows"]
                ] == [(2, 4), (5, 6), (8, 8)]
                assert job["window_strategy"] == "core-context-v3"
                assert job["analysis_version"] == 1
                assert job["previous_job_id"] is None
                assert job["workspace_id"] == job_id
                owned_pages = [
                    page
                    for window in job["windows"]
                    for page in range(
                        window["core_span"]["start"],
                        window["core_span"]["end"] + 1,
                    )
                ]
                assert owned_pages == [2, 3, 4, 5, 6, 8]
                assert job["windows"][0]["boundary_pages"] == [4, 5]
                assert 4 in job["windows"][1]["context_pages"]
                assert 5 in job["windows"][0]["context_pages"]
                normalized_boundary = prep._normalize_prep_response(
                    {
                        "candidates": [
                            {
                                "text": "Owned by the preceding core.",
                                "kind": "event",
                                "source_refs": [
                                    {"file": relative_source, "page": 4}
                                ],
                                "confidence": 0.5,
                                "possible_links": [],
                                "open_questions": [],
                            },
                            {
                                "text": "Owned by this core.",
                                "kind": "event",
                                "source_refs": [
                                    {"file": relative_source, "page": 5},
                                    {"file": relative_source, "page": 6},
                                ],
                                "confidence": 0.5,
                                "possible_links": [],
                                "open_questions": [],
                            },
                        ]
                    },
                    SimpleNamespace(
                        source_file=relative_source, source_pages=[4, 5, 6]
                    ),
                    core_pages={5, 6},
                )
                assert [
                    candidate["text"]
                    for candidate in normalized_boundary["candidates"]
                ] == ["Owned by this core."]
                assert job["scope"]["notes"] is None
                assert job["scope"]["objective"]

                started = await client.post(f"/api/domain/prep/jobs/{job_id}/run")
                assert started.status_code == 202, started.text

                completed = None
                for _ in range(100):
                    detail = await client.get(f"/api/domain/prep/jobs/{job_id}")
                    assert detail.status_code == 200, detail.text
                    completed = detail.json()["job"]
                    if completed["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.05)
                assert completed is not None
                assert completed["status"] == "completed", completed
                assert completed["candidate_count"] == 2, completed
                assert all(
                    window["status"] == "succeeded"
                    for window in completed["windows"]
                )

                candidates_response = await client.get(
                    f"/api/domain/prep/jobs/{job_id}/candidates"
                )
                assert candidates_response.status_code == 200, candidates_response.text
                candidates = candidates_response.json()["candidates"]
                assert len(candidates) == 2
                assert [ref["page"] for ref in candidates[0]["source_refs"]] == [2, 6]
                assert all(
                    ref["source_version"].startswith("sha256:")
                    for candidate in candidates
                    for ref in candidate["source_refs"]
                )
                assert all(
                    candidate["evidence_status"] == "model_candidate"
                    and candidate["review_state"] == "needs_review"
                    for candidate in candidates
                )

                candidate_id = candidates[0]["id"]
                edited = await client.patch(
                    f"/api/domain/shadow/candidates/{candidate_id}",
                    json={
                        "text": "A reviewed fact crosses its owned page boundary.",
                        "review_note": "Verified against both cited pages.",
                        "content_basis": "source_fact",
                    },
                )
                assert edited.status_code == 200, edited.text
                accepted = await client.post(
                    f"/api/domain/shadow/candidates/{candidate_id}/review",
                    json={"review_state": "accepted", "content_basis": "source_fact"},
                )
                assert accepted.status_code == 200, accepted.text
                promoted = await client.post(
                    f"/api/domain/shadow/candidates/{candidate_id}/promote",
                    json={"evidence_status": "source_fact"},
                )
                assert promoted.status_code == 200, promoted.text
                promotion_payload = promoted.json()
                assert promotion_payload["created"] is True
                assert promotion_payload["workspace_id"] == job_id
                promoted_fact = promotion_payload["fact"]
                assert promoted_fact["text"] == (
                    "A reviewed fact crosses its owned page boundary."
                )
                assert promoted_fact["evidence_status"] == "source_fact"
                assert [
                    reference["page"] for reference in promoted_fact["source_refs"]
                ] == [2, 6]
                assert promoted_fact["provenance"]["candidate_id"] == candidate_id
                assert promoted_fact["provenance"]["review_id"].startswith(
                    "shadow_review_"
                )

                promoted_again = await client.post(
                    f"/api/domain/shadow/candidates/{candidate_id}/promote",
                    json={"evidence_status": "source_fact"},
                )
                assert promoted_again.status_code == 200, promoted_again.text
                assert promoted_again.json()["created"] is False
                conflicting_promotion = await client.post(
                    f"/api/domain/shadow/candidates/{candidate_id}/promote",
                    json={"evidence_status": "inference"},
                )
                assert conflicting_promotion.status_code == 409

                workspace = await client.get(
                    f"/api/domain/workbench?example={job_id}"
                )
                assert workspace.status_code == 200, workspace.text
                workspace_payload = workspace.json()
                assert workspace_payload["has_seed"] is False
                assert workspace_payload["bundle"]["facts"] == [promoted_fact]
                assert workspace_payload["bundle"]["cards"] == []
                workspace_list = await client.get("/api/domain/workspaces")
                assert workspace_list.status_code == 200, workspace_list.text
                assert any(
                    item["id"] == job_id and item["kind"] == "prep"
                    for item in workspace_list.json()["workspaces"]
                )
                refreshed_job = (
                    await client.get(f"/api/domain/prep/jobs/{job_id}")
                ).json()["job"]
                assert refreshed_job["promoted_count"] == 1

                queue = await client.get(
                    "/api/domain/shadow/review-queue?review_state=all"
                )
                assert queue.status_code == 200, queue.text
                assert len(queue.json()["candidates"]) == 2

                task_batch = await client.post(
                    f"/api/domain/prep/jobs/{job_id}/candidates/review",
                    json={
                        "candidate_ids": [candidate["id"] for candidate in candidates],
                        "review_state": "accepted",
                        "review_note": "Accept the complete task-level review batch.",
                    },
                )
                assert task_batch.status_code == 200, task_batch.text
                assert len(task_batch.json()["candidates"]) == 2
                assert all(
                    candidate["review_state"] == "accepted"
                    for candidate in task_batch.json()["candidates"]
                )
                assert len(task_batch.json()["promotions"]) == 2
                chapter_sized = await client.post(
                    f"/api/domain/prep/jobs/{job_id}/candidates/review",
                    json={
                        "candidate_ids": [
                            f"shadow_candidate_chapter_{index}" for index in range(284)
                        ],
                        "review_state": "rejected",
                    },
                )
                assert chapter_sized.status_code == 404, chapter_sized.text
                assert isinstance(chapter_sized.json().get("detail"), str)
                too_many = await client.post(
                    f"/api/domain/prep/jobs/{job_id}/candidates/review",
                    json={
                        "candidate_ids": [f"shadow_candidate_fake_{index}" for index in range(1001)],
                        "review_state": "rejected",
                    },
                )
                assert too_many.status_code == 422, too_many.text
                assert isinstance(too_many.json().get("detail"), list)

                class LooseSchemaClient:
                    def chat(self, *_args, **_kwargs):
                        return json.dumps(
                            {
                                "candidates": [
                                    {
                                        "text": "A clue spans two adjacent pages.",
                                        "kind": "线索",
                                        "source_refs": ["p2", 3],
                                        "confidence": "high",
                                        "possible_links": "named witness",
                                        "open_questions": "confirm the exact wording",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )

                original_make_client = prep.make_client
                prep.make_client = lambda **_kwargs: LooseSchemaClient()
                try:
                    loose_created = await client.post(
                        "/api/domain/prep/jobs",
                        json={
                            "source_file": relative_source,
                            "page_range": "2-3",
                            "profile_id": "daggerheart",
                        },
                    )
                    assert loose_created.status_code == 200, loose_created.text
                    loose_job_id = loose_created.json()["job"]["id"]
                    loose_started = await client.post(
                        f"/api/domain/prep/jobs/{loose_job_id}/run"
                    )
                    assert loose_started.status_code == 202, loose_started.text
                    loose_completed = None
                    for _ in range(100):
                        detail = await client.get(
                            f"/api/domain/prep/jobs/{loose_job_id}"
                        )
                        loose_completed = detail.json()["job"]
                        if loose_completed["status"] not in {"queued", "running"}:
                            break
                        await asyncio.sleep(0.05)
                    assert loose_completed is not None
                    assert loose_completed["status"] == "completed", loose_completed
                    loose_candidates = (
                        await client.get(
                            f"/api/domain/prep/jobs/{loose_job_id}/candidates"
                        )
                    ).json()["candidates"]
                    assert len(loose_candidates) == 1
                    loose_candidate = loose_candidates[0]
                    assert loose_candidate["kind"] == "clue"
                    assert loose_candidate["confidence"] is None
                    assert [
                        reference["page"]
                        for reference in loose_candidate["source_refs"]
                    ] == [2, 3]
                    assert all(
                        reference["file"] == relative_source
                        for reference in loose_candidate["source_refs"]
                    )
                    assert loose_candidate["possible_links"] == ["named witness"]
                    assert loose_candidate["open_questions"] == [
                        "confirm the exact wording"
                    ]
                finally:
                    prep.make_client = original_make_client

                first_task_ids = [
                    window["shadow_task_id"]
                    for window in completed["windows"]
                    if window["shadow_task_id"]
                ]
                deleted = await client.delete(f"/api/domain/prep/jobs/{job_id}")
                assert deleted.status_code == 200, deleted.text
                missing_job = await client.get(f"/api/domain/prep/jobs/{job_id}")
                assert missing_job.status_code == 404
                for task_id in first_task_ids:
                    missing_task = await client.get(
                        f"/api/domain/shadow/tasks/{task_id}"
                    )
                    assert missing_task.status_code == 404
                    assert storage.list_shadow_runs(task_id) == []
                    assert storage.list_shadow_candidates(task_id, None) == []
                retained_workspace = await client.get(
                    f"/api/domain/workbench?example={job_id}"
                )
                assert retained_workspace.status_code == 200
                assert len(retained_workspace.json()["bundle"]["facts"]) == 2

            assert storage.load_domain_bundle("naimen_pilot") is None
            assert storage.load_session_state("naimen_pilot") is None
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir

    print("PASS: cross-page prep jobs generate durable source-bound candidates")


if __name__ == "__main__":
    asyncio.run(main())
