"""Offline contract for structured prep failure classification and JSON repair."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import shadow, storage  # noqa: E402
from backend.domain import ShadowTaskSpec  # noqa: E402
from backend.app.main import app  # noqa: E402


def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "errors.db"
        try:
            storage.init_db()
            task = shadow.create_shadow_task(
                ShadowTaskSpec.model_validate({
                    "idempotency_key": "error-contract",
                    "source_file": "fixture://error-contract",
                    "source_version": "v1",
                    "source_pages": [1],
                    "profile_id": "cthulhu-dark-2e",
                    "model_id": "fixture",
                    "prompt_version": "test",
                    "schema_version": "shadow-candidate-v1",
                    "input_excerpt": "fixture",
                })
            )[0]
            repaired = '{"candidates": [{"text": "x", "kind": "clue", "source_refs": [{"file": "fixture://error-contract", "page": 1}], "possible_links": [], "open_questions": []}'
            result = shadow.submit_shadow_result(task.id, raw_response=repaired)
            assert result[1].status == "succeeded"

            task2 = shadow.create_shadow_task(
                ShadowTaskSpec.model_validate({
                    "idempotency_key": "error-contract-2",
                    "source_file": "fixture://error-contract",
                    "source_version": "v1",
                    "source_pages": [1],
                    "profile_id": "cthulhu-dark-2e",
                    "model_id": "fixture",
                    "prompt_version": "test",
                    "schema_version": "shadow-candidate-v1",
                    "input_excerpt": "fixture",
                })
            )[0]
            _, failed, _ = shadow.submit_shadow_result(task2.id, raw_response='{"candidates": [')
            assert failed.status == "failed"
            assert failed.error_kind == "model_format"

            client = TestClient(app)
            response = client.get("/api/domain/workbench")
            assert response.status_code == 200
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: malformed model JSON is repaired when safe and classified when not")


if __name__ == "__main__":
    main()
