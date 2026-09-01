"""Offline P1.3 checks for isolated shadow-candidate review persistence."""
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


SOURCE_FILE = "fixture://p1-review"
SOURCE_VERSION = "fixture-v1"


def task_payload() -> dict:
    return {
        "idempotency_key": "p1-review-fixture",
        "source_file": SOURCE_FILE,
        "source_version": SOURCE_VERSION,
        "source_pages": [159],
        "profile_id": "cthulhu-dark-2e",
        "model_id": "offline-review-test",
        "prompt_version": "p1.3-test",
        "schema_version": "shadow-candidate-v1",
        "input_excerpt": "A witness saw a locked shop after midnight.",
    }


def valid_response() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "text": "A witness saw a locked shop after midnight.",
                    "kind": "clue",
                    "source_refs": [
                        {"file": SOURCE_FILE, "page": 159, "locator": "test excerpt"}
                    ],
                    "possible_links": ["fact_naimen_juju_location"],
                },
                {
                    "text": "The owner watches visitors and records distinguishing details.",
                    "kind": "npc",
                    "source_refs": [
                        {"file": SOURCE_FILE, "page": 159, "locator": "test excerpt"}
                    ],
                    "possible_links": ["fact_naimen_enkowan_front"],
                },
            ]
        }
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "shadow-review.db"
        try:
            storage.init_db()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                task = await client.post("/api/domain/shadow/tasks", json=task_payload())
                assert task.status_code == 200, task.text
                task_id = task.json()["task"]["id"]

                run = await client.post(
                    f"/api/domain/shadow/tasks/{task_id}/runs",
                    json={"raw_response": valid_response()},
                )
                assert run.status_code == 200, run.text
                original_candidates = run.json()["candidates"]
                assert len(original_candidates) == 2
                first_id = original_candidates[0]["id"]
                second_id = original_candidates[1]["id"]

                queue = await client.get("/api/domain/shadow/review-queue")
                assert queue.status_code == 200, queue.text
                assert [item["id"] for item in queue.json()["candidates"]] == [
                    first_id,
                    second_id,
                ]

                edited = await client.patch(
                    f"/api/domain/shadow/candidates/{first_id}",
                    json={
                        "text": "A witness saw the shop locked after midnight; retain it as a lead, not a conclusion.",
                        "review_note": "The source supports a nighttime observation, but not the cause.",
                        "content_basis": "inference",
                    },
                )
                assert edited.status_code == 200, edited.text
                accepted = await client.post(
                    f"/api/domain/shadow/candidates/{first_id}/review",
                    json={"review_state": "accepted", "content_basis": "inference"},
                )
                assert accepted.status_code == 200, accepted.text
                accepted_candidate = accepted.json()["candidate"]
                assert accepted_candidate["text"].startswith("A witness saw")
                assert accepted_candidate["evidence_status"] == "model_candidate"
                assert accepted_candidate["review_state"] == "accepted"
                assert accepted_candidate["review_note"].startswith("The source supports")
                # Editing and accepting are two explicit operations. History
                # keeps metadata for both, while the candidate stores only its
                # current text.
                assert len(accepted_candidate["review_history"]) == 2
                assert accepted_candidate["review_history"][0]["review_state"] == "needs_review"
                assert accepted_candidate["review_history"][0]["action"] == "edit"
                assert accepted_candidate["review_history"][1]["review_state"] == "accepted"
                assert accepted_candidate["review_history"][1]["action"] == "review"
                assert all("reviewed_text" not in event for event in accepted_candidate["review_history"])
                assert accepted_candidate["reviewed_at"] == accepted_candidate["review_history"][-1]["created_at"]

                pending = await client.get("/api/domain/shadow/review-queue")
                assert pending.status_code == 200, pending.text
                assert [item["id"] for item in pending.json()["candidates"]] == [second_id]
                accepted_queue = await client.get(
                    "/api/domain/shadow/review-queue?review_state=accepted"
                )
                assert accepted_queue.status_code == 200, accepted_queue.text
                assert [item["id"] for item in accepted_queue.json()["candidates"]] == [
                    first_id
                ]

                rejected = await client.post(
                    "/api/domain/shadow/review/batch",
                    json={
                        "candidate_ids": [second_id],
                        "review_state": "rejected",
                        "review_note": "The excerpt does not support this identity claim.",
                    },
                )
                assert rejected.status_code == 200, rejected.text
                rejected_candidate = rejected.json()["candidates"][0]
                assert rejected_candidate["review_state"] == "rejected"
                assert rejected_candidate["review_note"].startswith("The excerpt does not")
                assert len(rejected_candidate["review_history"]) == 1

                all_candidates = await client.get(
                    "/api/domain/shadow/review-queue?review_state=all"
                )
                assert all_candidates.status_code == 200, all_candidates.text
                assert {item["review_state"] for item in all_candidates.json()["candidates"]} == {
                    "accepted",
                    "rejected",
                }

                reopened = await client.post(
                    "/api/domain/shadow/review/batch",
                    json={
                        "candidate_ids": [first_id, second_id],
                        "review_state": "needs_review",
                        "review_note": "Reopen for a page-by-page source check.",
                    },
                )
                assert reopened.status_code == 200, reopened.text
                assert len(reopened.json()["candidates"]) == 2
                assert all(
                    candidate["review_state"] == "needs_review"
                    for candidate in reopened.json()["candidates"]
                )
                assert reopened.json()["candidates"][0]["text"] == accepted_candidate["text"]

                detail = await client.get(f"/api/domain/shadow/tasks/{task_id}")
                assert detail.status_code == 200, detail.text
                assert len(detail.json()["candidates"]) == 2
                detail_candidates = {candidate["id"]: candidate for candidate in detail.json()["candidates"]}
                assert len(detail_candidates[first_id]["review_history"]) == 3
                assert len(detail_candidates[second_id]["review_history"]) == 2
                assert all(
                    "reviewed_text" not in event
                    for candidate in detail_candidates.values()
                    for event in candidate["review_history"]
                )

                missing = await client.post(
                    "/api/domain/shadow/candidates/shadow_candidate_missing/review",
                    json={"review_state": "accepted"},
                )
                assert missing.status_code == 404, missing.text

            assert storage.load_domain_bundle("naimen_pilot") is None
            assert storage.load_session_state("naimen_pilot") is None
        finally:
            storage.DB_PATH = original_db_path

    print("PASS: candidate reviews remain isolated, durable, and source-preserving")


if __name__ == "__main__":
    asyncio.run(main())
