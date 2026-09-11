"""Regression for dense prep windows exceeding the old 50-candidate limit."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm, shadow, storage  # noqa: E402
from backend.domain import ShadowTaskSpec  # noqa: E402


def main() -> None:
    fake_candidates = [
        {"text": f"fake observation {index}", "kind": "clue"}
        for index in range(59)
    ]
    fake_result = llm._fake_consolidation_output(
        [{"role": "user", "content": "WINDOW_CANDIDATES_JSON=" + json.dumps(fake_candidates)}]
    )
    assert len(fake_result["candidates"]) == 59

    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "capacity.db"
        try:
            storage.init_db()
            task = shadow.create_shadow_task(
                ShadowTaskSpec.model_validate(
                    {
                        "idempotency_key": "dense-window-capacity",
                        "source_file": "fixture://dense-window",
                        "source_version": "v1",
                        "source_pages": [63, 64],
                        "profile_id": "cthulhu-dark-2e",
                        "model_id": "fixture",
                        "prompt_version": "test",
                        "schema_version": "shadow-candidate-v1",
                        "input_excerpt": "dense fixture",
                        "task_kind": "prep_window",
                        "queue_visibility": "internal",
                    }
                )
            )[0]
            payload = {
                "candidates": [
                    {
                        "text": f"dense observation {index}",
                        "kind": "clue",
                        "source_refs": [
                            {
                                "file": "fixture://dense-window",
                                "page": 63 if index % 2 else 64,
                            }
                        ],
                        "possible_links": [],
                        "open_questions": [],
                    }
                    for index in range(59)
                ]
            }

            completed_task, completed_run, candidates = shadow.submit_shadow_result(
                task.id, raw_response=json.dumps(payload)
            )

            assert completed_task.status == "completed"
            assert completed_run.status == "succeeded"
            assert completed_run.candidate_count == 59
            assert len(candidates) == 59
            assert len(storage.list_shadow_candidates(task.id, None, None)) == 59
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: dense prep windows preserve all 59 candidates")


if __name__ == "__main__":
    main()
