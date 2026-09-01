"""Regression checks for the P0.3 runtime-review export endpoint."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.main import app  # noqa: E402
from backend.app import storage  # noqa: E402
from backend.domain import load_json  # noqa: E402


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "runtime-review-api.db"
        try:
            storage.init_db()
            fixture = load_json(
                ROOT / "backend" / "domain" / "examples" / "evidence_status_fixture.json"
            )
            storage.save_domain_bundle("evidence_status_fixture", fixture)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                json_response = await client.get(
                    "/api/domain/examples/evidence_status_fixture/session/review?format=json"
                )
                assert json_response.status_code == 200, json_response.text
                assert "application/json" in json_response.headers["content-type"]
                assert json_response.headers["content-disposition"] == (
                    'attachment; filename="workbench-session-review.json"'
                )
                review = json_response.json()
                assert review["schema_version"] == "1.0"
                assert review["example_id"] == "evidence_status_fixture"
                assert "summary" in review
                assert "privacy_scope" in review

                markdown_response = await client.get(
                    "/api/domain/examples/evidence_status_fixture/session/review?format=markdown"
                )
                assert markdown_response.status_code == 200, markdown_response.text
                assert "text/markdown" in markdown_response.headers["content-type"]
                assert markdown_response.headers["content-disposition"] == (
                    'attachment; filename="workbench-session-review.md"'
                )
                assert markdown_response.content.startswith("# 运行复盘".encode("utf-8"))
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()
    print("PASS: runtime review API exports JSON and Markdown")


if __name__ == "__main__":
    asyncio.run(main())
