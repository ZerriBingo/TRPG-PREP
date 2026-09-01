"""Offline contract for semantic page segmentation with mechanical fallback."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 7):
            page = document.new_page()
            page.insert_text(
                (72, 72),
                f"Location {page_number // 2 + 1}\n"
                f"A continuing scene description for page {page_number}.",
            )
        document.save(path)
    finally:
        document.close()


class SegmentationClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[list[dict]] = []

    def chat(self, messages, **_kwargs):
        self.calls.append(messages)
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(self.response, ensure_ascii=False)
        return json.dumps({"candidates": []})


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "semantic.pdf"
        make_source(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "semantic.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "semantic-fixture"})
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            assert job.segmentation_status == "pending"
            client = SegmentationClient(
                {
                    "segments": [
                        {"start": 1, "end": 2, "label": "开场地点"},
                        {"start": 3, "end": 5, "label": "调查地点"},
                        {"start": 6, "end": 6, "label": "收束"},
                    ]
                }
            )
            prepared = prep._prepare_semantic_windows(job, source_path, client)
            assert prepared.segmentation_status == "succeeded"
            assert [(span.start, span.end, span.label) for span in prepared.semantic_segments] == [
                (1, 2, "开场地点"),
                (3, 5, "调查地点"),
                (6, 6, "收束"),
            ]
            assert all(window.page_span.label for window in prepared.windows)
            assert any(window.boundary_basis == "semantic" for window in prepared.windows)
            assert len(client.calls) == 1

            fallback_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            fallback_client = SegmentationClient({"segments": [{"start": 99, "end": 99}]})
            fallback = prep._prepare_semantic_windows(fallback_job, source_path, fallback_client)
            assert fallback.segmentation_status == "fallback"
            assert fallback.semantic_segments == []
            assert fallback.windows[0].page_span.start == 1
            assert fallback.segmentation_error
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            storage.init_db()

    print("PASS: semantic segmentation owns valid boundaries and falls back mechanically")


if __name__ == "__main__":
    main()
