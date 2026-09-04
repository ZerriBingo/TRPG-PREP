"""Regression contract for lossless transport slices inside one semantic segment."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 6):
            page = document.new_page()
            lines = [
                f"PAGE-{page_number}-BEGIN unique-start-{page_number}",
                *(
                    f"Page {page_number} paragraph {index}: "
                    "The same semantic location continues with source-backed detail."
                    for index in range(42)
                ),
                f"PAGE-{page_number}-END unique-end-{page_number}",
            ]
            for index, line in enumerate(lines):
                page.insert_text((48, 48 + index * 11), line, fontsize=7)
        document.save(path)
    finally:
        document.close()


class SegmentationClient:
    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {"segments": [{"start": 1, "end": 5, "label": "同一语义地点"}]},
                ensure_ascii=False,
            )
        return json.dumps({"candidates": []})


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "oversized-semantic-segment.pdf"
        make_source(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "semantic-window.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "semantic-window-fixture"})
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-5",
                    profile_id="cthulhu-dark-2e",
                )
            )
            prepared = prep._prepare_semantic_windows(
                job, source_path, SegmentationClient()
            )

            assert len(prepared.semantic_segments) == 1
            assert (prepared.semantic_segments[0].start, prepared.semantic_segments[0].end) == (1, 5)
            assert len(prepared.windows) >= 2
            assert all(window.semantic_segment_id for window in prepared.windows)
            assert len({window.semantic_segment_id for window in prepared.windows}) == 1
            assert [
                window.segment_window_index for window in prepared.windows
            ] == list(range(1, len(prepared.windows) + 1))
            assert all(
                window.segment_window_count == len(prepared.windows)
                for window in prepared.windows
            )

            owned_pages = [
                page
                for window in prepared.windows
                for page in window.core_span.pages()
            ]
            assert owned_pages == [1, 2, 3, 4, 5]
            assert any(
                window.split_reason == "transport_budget"
                for window in prepared.windows[:-1]
            )

            page_texts = prep._page_texts(source_path, [prepared.semantic_segments[0]])
            excerpts = []
            for window in prepared.windows:
                excerpt, truncated_pages = prep._window_excerpt(source_path, window)
                assert truncated_pages == []
                assert len(excerpt) <= prep.MAX_WINDOW_INPUT_CHARS
                excerpts.append(excerpt)
            for page, text in page_texts.items():
                assert any(text in excerpt for excerpt in excerpts), page
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            storage.init_db()

    print("PASS: oversized semantic segments retain one logical unit and full source text")


if __name__ == "__main__":
    main()
