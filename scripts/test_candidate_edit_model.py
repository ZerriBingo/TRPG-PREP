"""Regression checks for current-record candidate edits and replacements."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain.prep import ExtractionWindow, PageSpan, PrepJob, PrepScope  # noqa: E402


def task_payload() -> dict:
    return {
        "idempotency_key": "candidate-edit-model-fixture",
        "source_file": "fixture://candidate-edit",
        "source_version": "fixture-v1",
        "source_pages": [10, 11],
        "profile_id": "cthulhu-dark-2e",
        "model_id": "offline-candidate-edit",
        "prompt_version": "candidate-edit-v1",
        "schema_version": "shadow-candidate-v1",
        "input_excerpt": "A witness saw the station and the archive.",
    }


def response_payload() -> dict:
    return {
        "candidates": [
            {
                "text": "The station has a locked side entrance.",
                "kind": "location",
                "source_refs": [
                    {"file": "fixture://candidate-edit", "page": 10, "locator": "station"}
                ],
                "possible_links": [],
                "open_questions": [],
            },
            {
                "text": "The archive keeps a duplicate ledger.",
                "kind": "clue",
                "source_refs": [
                    {"file": "fixture://candidate-edit", "page": 11, "locator": "ledger"}
                ],
                "possible_links": [],
                "open_questions": [],
            },
        ]
    }


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "candidate-edit.db"
        try:
            storage.init_db()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                task_response = await client.post("/api/domain/shadow/tasks", json=task_payload())
                assert task_response.status_code == 200, task_response.text
                task_id = task_response.json()["task"]["id"]
                run_response = await client.post(
                    f"/api/domain/shadow/tasks/{task_id}/runs",
                    json={"raw_response": json.dumps(response_payload())},
                )
                assert run_response.status_code == 200, run_response.text
                candidates = run_response.json()["candidates"]
                first_id, second_id = [item["id"] for item in candidates]

                # Attach the shadow task to a real prep-job-shaped row so the
                # promotion seam exercises the same ownership lookup as the
                # bookshelf workflow.
                prep_id = "prep_job_candidate_edit_fixture"
                prep_scope = PrepScope(
                    source_file="fixture://candidate-edit",
                    source_version="fixture-v1",
                    page_spans=[PageSpan(start=10, end=11)],
                    profile_id="cthulhu-dark-2e",
                    objective="fixture",
                )
                prep_job = PrepJob(
                    id=prep_id,
                    scope=prep_scope,
                    model_id="offline-candidate-edit",
                    prompt_version="candidate-edit-v1",
                    schema_version="shadow-candidate-v1",
                    workspace_id=prep_id,
                    windows=[
                        ExtractionWindow(
                            id="prep_window_candidate_edit_fixture",
                            page_span=PageSpan(start=10, end=11),
                            shadow_task_id=task_id,
                        )
                    ],
                    created_at=storage.now(),
                    updated_at=storage.now(),
                )
                storage.create_prep_job(prep_job.model_dump(mode="json"))

                edit_response = await client.patch(
                    f"/api/domain/shadow/candidates/{first_id}",
                    json={
                        "text": "The station has a locked side entrance and a service corridor.",
                        "content_basis": "inference",
                        "review_note": "The added corridor is a GM inference.",
                    },
                )
                assert edit_response.status_code == 200, edit_response.text
                edited = edit_response.json()["candidate"]
                assert edited["text"].startswith("The station has a locked")
                assert "reviewed_text" not in edited
                assert edited["review_state"] == "needs_review"
                assert edited["content_basis"] == "inference"
                assert edited["review_history"][-1]["action"] == "edit"
                assert "note" in edited["review_history"][-1]
                assert "reviewed_text" not in edited["review_history"][-1]

                cleared_response = await client.patch(
                    f"/api/domain/shadow/candidates/{first_id}",
                    json={"review_note": ""},
                )
                assert cleared_response.status_code == 200, cleared_response.text
                cleared = cleared_response.json()["candidate"]
                assert cleared["review_note"] is None
                assert cleared["review_history"][-1]["action"] == "edit"
                assert cleared["review_history"][-1]["note"] is None
                assert "review_note" in cleared["review_history"][-1]["field_paths"]

                source_edit_response = await client.patch(
                    f"/api/domain/shadow/candidates/{first_id}",
                    json={
                        "source_refs": [
                            {
                                "file": "fixture://candidate-edit",
                                "page": 10,
                                "locator": "station",
                            },
                            {
                                "file": "fixture://candidate-edit",
                                "page": 11,
                                "locator": "corridor",
                            },
                        ]
                    },
                )
                assert source_edit_response.status_code == 200, source_edit_response.text
                source_edited = source_edit_response.json()["candidate"]
                assert [item["page"] for item in source_edited["source_refs"]] == [10, 11]
                source_event = source_edited["review_history"][-1]
                assert "source_refs" in source_event["field_paths"]
                assert source_event["source_changes"] == ["source_refs"]

                # Legacy payloads are accepted only as an input migration; a
                # subsequent write emits the current-record shape. The
                # current record remains authoritative over stale duplicate
                # model output.
                legacy = {
                    **source_edited,
                    "reviewed_text": "legacy duplicate",
                    "review_history": [
                        {
                            "id": "shadow_review_legacy",
                            "review_state": "needs_review",
                            "reviewed_text": "legacy duplicate",
                            "review_note": "legacy note",
                            "created_at": "2026-08-30T00:00:00+00:00",
                        }
                    ],
                    "reviewed_at": "2026-08-30T00:00:00+00:00",
                    "review_note": "legacy note",
                }
                from backend.domain.shadow import ShadowCandidate  # noqa: PLC0415

                loaded_legacy = ShadowCandidate.model_validate(legacy)
                assert loaded_legacy.text == source_edited["text"]
                assert "reviewed_text" not in loaded_legacy.model_dump(mode="json")

                accept_response = await client.post(
                    f"/api/domain/shadow/candidates/{first_id}/review",
                    json={"review_state": "accepted", "content_basis": "inference"},
                )
                assert accept_response.status_code == 200, accept_response.text

                promote_response = await client.post(
                    f"/api/domain/shadow/candidates/{first_id}/promote",
                    json={"evidence_status": "inference"},
                )
                assert promote_response.status_code == 200, promote_response.text
                fact = promote_response.json()["fact"]
                assert fact["links"] == []
                workspace_id = promote_response.json()["workspace_id"]

                split_response = await client.post(
                    f"/api/domain/shadow/candidates/{second_id}/split",
                    json={
                        "content_basis": "source_fact",
                        "parts": [
                            {
                                "text": "The archive keeps a duplicate ledger.",
                                "kind": "clue",
                                "source_refs": [{"file": "fixture://candidate-edit", "page": 11}],
                            },
                            {
                                "text": "The ledger is hidden behind a false panel.",
                                "kind": "location",
                                "source_refs": [{"file": "fixture://candidate-edit", "page": 11}],
                            },
                        ],
                    },
                )
                assert split_response.status_code == 200, split_response.text
                split_candidates = split_response.json()["candidates"]
                assert len(split_candidates) == 2
                assert all(item["review_state"] == "needs_review" for item in split_candidates)
                assert all(item["review_history"][-1]["action"] == "split" for item in split_candidates)

                merge_response = await client.post(
                    "/api/domain/shadow/candidates/merge",
                    json={
                        "candidate_ids": [item["id"] for item in split_candidates],
                        "text": "The archive's duplicate ledger is hidden behind a false panel.",
                        "kind": "clue",
                        "content_basis": "source_fact",
                    },
                )
                assert merge_response.status_code == 200, merge_response.text
                merged = merge_response.json()["candidate"]
                assert merged["review_state"] == "needs_review"
                assert merged["review_history"][-1]["action"] == "merge"

                # Candidate edits are isolated from the promoted fact snapshot.
                second_edit = await client.patch(
                    f"/api/domain/shadow/candidates/{first_id}",
                    json={"text": "Changed after promotion", "content_basis": "inference"},
                )
                assert second_edit.status_code == 200, second_edit.text
                saved = storage.load_domain_bundle(workspace_id)
                assert saved is not None
                assert saved[0]["facts"][0]["id"] == fact["id"]
                assert saved[0]["facts"][0]["text"] == fact["text"]
        finally:
            storage.DB_PATH = original_db_path

    print("PASS: candidate edits, split/merge replacements, and promotion isolation")


if __name__ == "__main__":
    asyncio.run(main())
