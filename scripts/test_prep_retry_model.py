"""Regression check: retrying a prep job uses the current model snapshot."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 3):
            page = document.new_page()
            page.insert_text((72, 72), f"Retry fixture page {page_number}.")
        document.save(path)
    finally:
        document.close()


class EmptyCandidateClient:
    def chat(self, *_args, **_kwargs) -> str:
        return '{"candidates": []}'


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "retry-source.pdf"
        make_source_pdf(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "retry-model.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "old-model",
                    "fake": True,
                }
            )
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-2",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(
                job.id, model_id="old-model", fake_model=True
            )
            prep._execute_window(
                started,
                started.windows[0],
                source_path,
                EmptyCandidateClient(),
            )

            failed = prep._job_from_store(job.id)
            failed = prep._save_job(
                failed,
                status="failed",
                windows=[
                    failed.windows[0].model_copy(
                        update={"status": "failed", "error": "forced retry"}
                    ).model_dump(mode="json")
                ],
            )
            old_task_id = failed.windows[0].shadow_task_id
            assert old_task_id is not None

            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "new-model",
                    "fake": True,
                }
            )
            retried = prep.start_prep_job(
                job.id, model_id="new-model", fake_model=True
            )
            assert retried.model_id == "new-model"
            assert retried.fake_model is True
            prep._execute_window(
                retried,
                retried.windows[0],
                source_path,
                EmptyCandidateClient(),
            )
            completed = prep._job_from_store(job.id)
            new_task_id = completed.windows[0].shadow_task_id
            assert new_task_id and new_task_id != old_task_id
            old_task = storage.load_shadow_task(old_task_id)
            new_task = storage.load_shadow_task(new_task_id)
            assert old_task["model_id"] == "old-model"
            assert new_task["model_id"] == "new-model"
            assert old_task["idempotency_key"] != new_task["idempotency_key"]
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            storage.init_db()

    print("PASS: prep retries use the current model and isolated shadow task")


if __name__ == "__main__":
    main()
