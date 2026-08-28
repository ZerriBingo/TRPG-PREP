"""Offline P1.1 checks for isolated shadow-model task and review records."""
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


SOURCE_FILE = "fixture://p1-shadow"
SOURCE_VERSION = "fixture-v1"


def task_payload(key: str) -> dict:
    return {
        "idempotency_key": key,
        "source_file": SOURCE_FILE,
        "source_version": SOURCE_VERSION,
        "source_pages": [159, 160],
        "profile_id": "cthulhu-dark-2e",
        "model_id": "offline-shadow-test",
        "prompt_version": "p1.1-test",
        "schema_version": "shadow-candidate-v1",
        "input_excerpt": "A witness heard singing inside the locked house.",
    }


def valid_response(page: int = 159) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "text": "A witness reports singing inside the locked house.",
                    "kind": "clue",
                    "source_refs": [
                        {
                            "file": SOURCE_FILE,
                            "page": page,
                            "locator": "test excerpt",
                        }
                    ],
                    "confidence": 0.74,
                    "possible_links": ["fact_naimen_juju_house"],
                    "open_questions": ["Who was singing?"],
                }
            ]
        }
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "shadow-mode.db"
        try:
            storage.init_db()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                first = await client.post(
                    "/api/domain/shadow/tasks", json=task_payload("same-input")
                )
                assert first.status_code == 200, first.text
                assert first.json()["created"] is True
                task_id = first.json()["task"]["id"]

                replay = await client.post(
                    "/api/domain/shadow/tasks", json=task_payload("same-input")
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["created"] is False
                assert replay.json()["task"]["id"] == task_id

                conflicting = task_payload("same-input")
                conflicting["source_pages"] = [161]
                collision = await client.post(
                    "/api/domain/shadow/tasks", json=conflicting
                )
                assert collision.status_code == 409, collision.text

                truncated = await client.post(
                    f"/api/domain/shadow/tasks/{task_id}/runs",
                    json={"raw_response": '{"candidates": ['},
                )
                assert truncated.status_code == 200, truncated.text
                assert truncated.json()["task"]["status"] == "failed"
                assert truncated.json()["run"]["parse_error"]
                assert truncated.json()["candidates"] == []

                retry = await client.post(
                    f"/api/domain/shadow/tasks/{task_id}/runs",
                    json={"raw_response": valid_response()},
                )
                assert retry.status_code == 200, retry.text
                assert retry.json()["task"]["status"] == "completed"
                assert retry.json()["run"]["attempt"] == 2
                assert retry.json()["run"]["candidate_count"] == 1
                candidate = retry.json()["candidates"][0]
                assert candidate["evidence_status"] == "model_candidate"
                assert candidate["review_state"] == "needs_review"
                assert candidate["source_refs"][0]["source_version"] == SOURCE_VERSION

                detail = await client.get(f"/api/domain/shadow/tasks/{task_id}")
                assert detail.status_code == 200, detail.text
                assert [run["status"] for run in detail.json()["runs"]] == [
                    "failed",
                    "succeeded",
                ]
                assert len(detail.json()["candidates"]) == 1

                bad_source = await client.post(
                    "/api/domain/shadow/tasks", json=task_payload("bad-source")
                )
                bad_task_id = bad_source.json()["task"]["id"]
                out_of_range = await client.post(
                    f"/api/domain/shadow/tasks/{bad_task_id}/runs",
                    json={"raw_response": valid_response(page=161)},
                )
                assert out_of_range.status_code == 200, out_of_range.text
                assert out_of_range.json()["task"]["status"] == "failed"
                assert "outside the task range" in out_of_range.json()["run"]["parse_error"]

                network_task = await client.post(
                    "/api/domain/shadow/tasks", json=task_payload("network-retry")
                )
                network_task_id = network_task.json()["task"]["id"]
                offline = await client.post(
                    f"/api/domain/shadow/tasks/{network_task_id}/runs",
                    json={"transport_error": "connection refused"},
                )
                assert offline.status_code == 200, offline.text
                assert offline.json()["run"]["transport_error"] == "connection refused"
                network_retry = await client.post(
                    f"/api/domain/shadow/tasks/{network_task_id}/runs",
                    json={"raw_response": valid_response()},
                )
                assert network_retry.status_code == 200, network_retry.text
                assert network_retry.json()["task"]["status"] == "completed"

                cancelled_task = await client.post(
                    "/api/domain/shadow/tasks", json=task_payload("cancelled")
                )
                cancelled_task_id = cancelled_task.json()["task"]["id"]
                cancelled = await client.post(
                    f"/api/domain/shadow/tasks/{cancelled_task_id}/cancel"
                )
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["task"]["status"] == "cancelled"
                blocked = await client.post(
                    f"/api/domain/shadow/tasks/{cancelled_task_id}/runs",
                    json={"raw_response": valid_response()},
                )
                assert blocked.status_code == 409, blocked.text

                queue = await client.get("/api/domain/shadow/review-queue")
                assert queue.status_code == 200, queue.text
                assert len(queue.json()["candidates"]) == 2

            assert storage.load_domain_bundle("naimen_pilot") is None
            assert storage.load_session_state("naimen_pilot") is None
        finally:
            storage.DB_PATH = original_db_path

    print("PASS: shadow tasks stay isolated through failure, retry, cancel, and review")


if __name__ == "__main__":
    asyncio.run(main())
