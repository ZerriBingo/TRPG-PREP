"""Regression contracts for broad semantic plans and reducer timeouts."""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.app.llm import FakeLLM  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path, *, pages: int) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, pages + 1):
            page = document.new_page()
            for line_number in range(28):
                page.insert_text(
                    (42, 42 + line_number * 10),
                    (
                        f"Location {page_number} detail {line_number}: "
                        "source-backed scene information and investigation context."
                    ),
                    fontsize=7,
                )
        document.save(path)
    finally:
        document.close()


def json_line(content: str, marker: str, default):
    match = re.search(rf"^{re.escape(marker)}(.+)$", content, re.MULTILINE)
    return json.loads(match.group(1)) if match else default


class BroadPlanClient:
    def __init__(self) -> None:
        self.planning_tasks: list[str] = []

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment:refine]" in content:
            self.planning_tasks.append("refine")
            pages = json_line(content, "SELECTED_PAGES_JSON=", [])
            return json.dumps(
                {
                    "segments": [
                        {
                            "start": start,
                            "end": min(start + 1, pages[-1]),
                            "label": f"地点 {start}",
                        }
                        for start in range(pages[0], pages[-1] + 1, 2)
                    ]
                },
                ensure_ascii=False,
            )
        if "[TASK:prep:segment]" in content:
            self.planning_tasks.append("initial")
            pages = json_line(content, "SELECTED_PAGES_JSON=", [])
            return json.dumps(
                {
                    "segments": [
                        {
                            "start": pages[0],
                            "end": pages[-1],
                            "label": "整章",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return '{"candidates": []}'


class TimeoutThenSmallBatchClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.consolidation_sizes: list[int] = []

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {"segments": [{"start": 1, "end": 1, "label": "密集地点"}]},
                ensure_ascii=False,
            )
        if "[TASK:prep:consolidate]" in content:
            candidates = json_line(content, "WINDOW_CANDIDATES_JSON=", [])
            self.consolidation_sizes.append(len(candidates))
            if len(candidates) > 10:
                raise RuntimeError("API 请求超时（120s）")
            return json.dumps(
                {
                    "candidates": [
                        {
                            **candidate,
                            "source_refs": [
                                {"file": self.source_file, **reference}
                                for reference in candidate["source_refs"]
                            ],
                            "confidence": 0.6,
                        }
                        for candidate in candidates
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": f"互不重复的来源事实 {index}。",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": 1}],
                        "confidence": 0.6,
                        "possible_links": [],
                        "open_questions": [],
                    }
                    for index in range(37)
                ]
            },
            ensure_ascii=False,
        )


def execute_with_client(job_id: str, client) -> None:
    original_make_client = prep.make_client
    prep.make_client = lambda **_kwargs: client
    try:
        prep.execute_prep_job(job_id)
    finally:
        prep.make_client = original_make_client


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    original_window_limit = prep.MAX_WINDOW_INPUT_CHARS
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        storage.DB_PATH = temp_path / "semantic-resilience.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "semantic-resilience"})

            prep.MAX_WINDOW_INPUT_CHARS = 4000
            broad_source = temp_path / "broad-plan.pdf"
            make_source(broad_source, pages=12)
            broad_relative = broad_source.relative_to(ROOT).as_posix()
            broad_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=broad_relative,
                    page_range="1-12",
                    profile_id="cthulhu-dark-2e",
                )
            )
            broad_client = BroadPlanClient()
            broad_started = prep.start_prep_job(
                broad_job.id,
                model_id="semantic-resilience",
                fake_model=True,
            )
            execute_with_client(broad_started.id, broad_client)
            prepared = prep.get_prep_job(broad_started.id)
            assert prepared.status == "completed", prepared
            assert broad_client.planning_tasks == ["initial", "refine"]
            assert [(item.start, item.end) for item in prepared.semantic_segments] == [
                (1, 2),
                (3, 4),
                (5, 6),
                (7, 8),
                (9, 10),
                (11, 12),
            ]
            assert max(window.segment_window_count for window in prepared.windows) <= 3

            fake_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=broad_relative,
                    page_range="1-12",
                    profile_id="cthulhu-dark-2e",
                )
            )
            fake_started = prep.start_prep_job(
                fake_job.id,
                model_id="semantic-resilience",
                fake_model=True,
            )
            execute_with_client(
                fake_started.id, FakeLLM({"model": "semantic-resilience"})
            )
            fake_prepared = prep.get_prep_job(fake_started.id)
            assert fake_prepared.status == "completed", fake_prepared
            assert len(fake_prepared.semantic_segments) > 1

            prep.MAX_WINDOW_INPUT_CHARS = original_window_limit
            timeout_source = temp_path / "timeout.pdf"
            make_source(timeout_source, pages=1)
            timeout_relative = timeout_source.relative_to(ROOT).as_posix()
            timeout_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=timeout_relative,
                    page_range="1",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(
                timeout_job.id,
                model_id="semantic-resilience",
                fake_model=True,
            )
            timeout_client = TimeoutThenSmallBatchClient(timeout_relative)
            execute_with_client(started.id, timeout_client)
            completed = prep.get_prep_job(started.id)
            assert completed.status == "completed", completed
            assert timeout_client.consolidation_sizes[0] == 37
            assert any(size <= 10 for size in timeout_client.consolidation_sizes)
            assert len(prep.list_prep_job_candidates(started.id)) == 37
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            prep.MAX_WINDOW_INPUT_CHARS = original_window_limit
            storage.init_db()

    print("PASS: broad semantic plans refine and timed-out reducers shrink without loss")


if __name__ == "__main__":
    main()
