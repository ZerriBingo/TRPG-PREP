"""Public source-file lifecycle contracts for the current workbench."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz
import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.main import app  # noqa: E402


def pdf_bytes() -> bytes:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "Source lifecycle fixture")
        return document.tobytes()
    finally:
        document.close()


async def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        storage.DB_PATH = temp_path / "source-files.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "source-fixture"})
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                payload = pdf_bytes()
                first = await client.post(
                    "/api/domain/source-files",
                    files={"file": ("chapter.pdf", payload, "application/pdf")},
                )
                assert first.status_code == 200, first.text
                first_data = first.json()
                assert first_data["reused"] is False
                source_file = first_data["file"]

                duplicate = await client.post(
                    "/api/domain/source-files",
                    files={"file": ("renamed-copy.pdf", payload, "application/pdf")},
                )
                assert duplicate.status_code == 200, duplicate.text
                duplicate_data = duplicate.json()
                assert duplicate_data["reused"] is True
                assert duplicate_data["file"] == source_file

                listed = await client.get("/api/domain/source-files")
                assert listed.status_code == 200, listed.text
                assert listed.json()["uploads"] == [source_file]
                assert listed.json()["upload_items"][0]["file"] == source_file
                assert listed.json()["upload_items"][0]["referenced"] is False

                job = await client.post(
                    "/api/domain/prep/jobs",
                    json={
                        "source_file": source_file,
                        "page_range": "1",
                        "profile_id": "cthulhu-dark-2e",
                    },
                )
                assert job.status_code == 200, job.text

                blocked = await client.delete(
                    "/api/domain/source-files",
                    params={"file": source_file},
                )
                assert blocked.status_code == 409, blocked.text
                assert "备团任务" in str(blocked.json().get("detail"))

                await client.delete(f"/api/domain/prep/jobs/{job.json()['job']['id']}")
                deleted = await client.post(
                    "/api/domain/source-files/delete",
                    params={"file": source_file},
                )
                assert deleted.status_code == 200, deleted.text
                assert not (ROOT / source_file).exists()
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            storage.init_db()

    print("PASS: source files are deduplicated, listed, and reference-protected")


if __name__ == "__main__":
    asyncio.run(main())
