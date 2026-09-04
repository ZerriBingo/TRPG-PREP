"""Regression contract for segment-level candidate consolidation."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, shadow, storage  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 4):
            page = document.new_page()
            page.insert_text(
                (48, 48),
                f"LOCATION-BEGIN-{page_number}\n"
                + (f"The same location continues with supported detail on page {page_number}.\n" * 30)
                + f"LOCATION-END-{page_number}",
                fontsize=7,
            )
        document.save(path)
    finally:
        document.close()


class SegmentClient:
    def __init__(
        self,
        source_file: str,
        *,
        echo_candidate_id: bool = False,
        legacy_reducer_shape: bool = False,
        top_level_questions: list[str] | None = None,
    ) -> None:
        self.source_file = source_file
        self.echo_candidate_id = echo_candidate_id
        self.legacy_reducer_shape = legacy_reducer_shape
        self.top_level_questions = top_level_questions
        self.calls: list[str] = []

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        self.calls.append(content)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {"segments": [{"start": 1, "end": 3, "label": "同一地点"}]},
                ensure_ascii=False,
            )
        if "[TASK:prep:consolidate]" in content:
            candidate = {
                "text": "同一地点的入口与地下室信息属于一个完整地点事实。",
                "kind": "location",
                "source_refs": [
                    {"file": self.source_file, "page": 1},
                    {"file": self.source_file, "page": 3},
                ],
                "confidence": 0.8,
                "possible_links": [],
                "open_questions": [],
            }
            if self.legacy_reducer_shape:
                candidate["source_refs"] = [
                    {"source_file": self.source_file, "page": 1},
                    {"source_file": self.source_file, "page": 3},
                ]
            if self.echo_candidate_id:
                # Some models echo the internal input identifier during reduction.
                candidate["candidate_id"] = "shadow_candidate_window_1"
            response = {"candidates": [candidate]}
            if self.top_level_questions is not None:
                response["open_questions"] = self.top_level_questions
            return json.dumps(response, ensure_ascii=False)
        pages_marker = "SOURCE_PAGES_JSON="
        pages_line = next(
            line for line in content.splitlines() if line.startswith(pages_marker)
        )
        fact_page = json.loads(pages_line.removeprefix(pages_marker))[0]
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": "同一地点的局部描述。",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": fact_page}],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            },
            ensure_ascii=False,
        )


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    original_limit = prep.MAX_WINDOW_INPUT_CHARS
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "consolidation.pdf"
        make_source(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "consolidation.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        prep.MAX_WINDOW_INPUT_CHARS = 900
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "consolidation-fixture"})
            client = SegmentClient(relative_source)
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(job.id, model_id="consolidation-fixture", fake_model=True)
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: client
            try:
                prep.execute_prep_job(started.id)
            finally:
                prep.make_client = original_make_client
            completed = prep.get_prep_job(job.id)
            assert completed.status == "completed", completed
            assert len(completed.windows) >= 2
            assert len({window.semantic_segment_id for window in completed.windows}) == 1
            consolidation_ids = {
                window.consolidation_task_id
                for window in completed.windows
                if window.consolidation_task_id
            }
            assert len(consolidation_ids) == 1
            consolidation_id = next(iter(consolidation_ids))
            assert all(
                window.consolidation_status == "succeeded" for window in completed.windows
            )
            candidates = prep.list_prep_job_candidates(job.id)
            assert len(candidates) == 1, candidates
            assert candidates[0]["candidate_role"] == "segment_result"
            assert [ref["page"] for ref in candidates[0]["source_refs"]] == [1, 3]

            raw_tasks = storage.list_shadow_tasks()
            raw_window_tasks = [item for item in raw_tasks if item["task_kind"] == "prep_window"]
            consolidated_tasks = [item for item in raw_tasks if item["id"] == consolidation_id]
            assert len(raw_window_tasks) == len(completed.windows)
            assert all(item["queue_visibility"] == "internal" for item in raw_window_tasks)
            assert len(consolidated_tasks) == 1
            assert consolidated_tasks[0]["queue_visibility"] == "review"
            assert len(storage.list_shadow_candidates(raw_window_tasks[0]["id"], None, None)) == 1
            assert len(storage.list_shadow_candidates(raw_window_tasks[0]["id"], None)) == 0
            assert [task.id for task in shadow.list_shadow_tasks()] == [consolidation_id]

            echo_client = SegmentClient(relative_source, echo_candidate_id=True)
            echo_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            echo_started = prep.start_prep_job(
                echo_job.id, model_id="consolidation-echo-fixture", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: echo_client
            try:
                prep.execute_prep_job(echo_started.id)
            finally:
                prep.make_client = original_make_client
            echo_completed = prep.get_prep_job(echo_job.id)
            assert echo_completed.status == "completed", echo_completed
            echo_candidates = prep.list_prep_job_candidates(echo_job.id)
            assert len(echo_candidates) == 1, echo_candidates
            assert "candidate_id" not in echo_candidates[0]

            legacy_client = SegmentClient(relative_source, legacy_reducer_shape=True)
            legacy_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            legacy_started = prep.start_prep_job(
                legacy_job.id, model_id="consolidation-legacy-fixture", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: legacy_client
            try:
                prep.execute_prep_job(legacy_started.id)
            finally:
                prep.make_client = original_make_client
            legacy_completed = prep.get_prep_job(legacy_job.id)
            assert legacy_completed.status == "completed", legacy_completed
            assert len(prep.list_prep_job_candidates(legacy_job.id)) == 1

            invalid_client = SegmentClient(
                relative_source,
                top_level_questions=["必须保留归属的非空问题"],
            )
            invalid_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            invalid_started = prep.start_prep_job(
                invalid_job.id, model_id="consolidation-invalid-fixture", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: invalid_client
            try:
                prep.execute_prep_job(invalid_started.id)
            finally:
                prep.make_client = original_make_client
            invalid_completed = prep.get_prep_job(invalid_job.id)
            assert invalid_completed.status == "partial"
            assert all(
                window.consolidation_status == "failed"
                for window in invalid_completed.windows
            )
            assert "open_questions" in (invalid_completed.windows[0].consolidation_error or "")
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            prep.MAX_WINDOW_INPUT_CHARS = original_limit
            storage.init_db()

    print("PASS: semantic transport windows consolidate into one review candidate")


if __name__ == "__main__":
    main()
