"""Regression contract for interrupted semantic-consolidation recovery."""

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

from backend.app import prep, shadow, storage  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 4):
            page = document.new_page()
            page.insert_text(
                (48, 48),
                (f"Location detail on page {page_number}.\n" * 48),
                fontsize=7,
            )
        document.save(path)
    finally:
        document.close()


def json_line(content: str, marker: str, default):
    match = re.search(rf"^{re.escape(marker)}(.+)$", content, re.MULTILINE)
    return json.loads(match.group(1)) if match else default


class InterruptedConsolidationClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps({"segments": [{"start": 1, "end": 3, "label": "Location"}]})
        if "[TASK:prep:consolidate]" in content:
            raise KeyboardInterrupt("simulated process stop during consolidation")
        pages = json_line(content, "SOURCE_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": "Window observation.",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": pages[0]}],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            }
        )


class CompletedConsolidationClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        assert "[TASK:prep:consolidate]" in content
        pages = json_line(content, "SEMANTIC_SEGMENT_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": "Recovered segment result.",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": pages[0]}],
                        "confidence": 0.8,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            }
        )


class NoChatClient:
    def chat(self, *_args, **_kwargs):
        raise AssertionError("a completed consolidation task must be reused")


class PartialMultiSegmentClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.consolidation_calls = 0
        self.retrying = False

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {
                    "segments": [
                        {"start": 1, "end": 2, "label": "First location"},
                        {"start": 3, "end": 3, "label": "Second location"},
                    ]
                }
            )
        if "[TASK:prep:consolidate]" in content:
            self.consolidation_calls += 1
            pages = json_line(content, "SEMANTIC_SEGMENT_PAGES_JSON=", [1])
            if self.retrying and pages == [1, 2]:
                raise AssertionError("a successful segment must not be re-consolidated")
            if pages == [3] and not self.retrying:
                return "not-json"
            return json.dumps(
                {
                    "candidates": [
                        {
                            "text": f"Segment result for p{pages[0]}.",
                            "kind": "location",
                            "source_refs": [{"file": self.source_file, "page": pages[0]}],
                            "confidence": 0.8,
                            "possible_links": [],
                            "open_questions": [],
                        }
                    ]
                }
            )
        pages = json_line(content, "SOURCE_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": f"Window observation p{pages[0]}.",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": pages[0]}],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            }
        )


def run_job(job_id: str, client) -> None:
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
        source_path = temp_path / "semantic-recovery.pdf"
        make_source(source_path)
        source_file = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "semantic-recovery.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        prep.MAX_WINDOW_INPUT_CHARS = 700
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "recovery-model"})
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(job.id, model_id="recovery-model", fake_model=True)
            try:
                run_job(started.id, InterruptedConsolidationClient(source_file))
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("the fixture must interrupt semantic consolidation")

            interrupted = prep._job_from_store(job.id)
            assert interrupted.status == "running"
            assert all(window.status == "succeeded" for window in interrupted.windows)
            queued_tasks = [
                task
                for task in storage.list_shadow_tasks()
                if task["idempotency_key"].startswith(job.id + ":consolidate:")
            ]
            assert len(queued_tasks) == 1
            assert queued_tasks[0]["status"] == "queued"

            recovered = prep.get_prep_job(job.id)
            assert recovered.status == "partial", recovered
            assert all(window.consolidation_status == "failed" for window in recovered.windows), (
                recovered
            )
            assert all(
                window.consolidation_error and "interrupted" in window.consolidation_error
                for window in recovered.windows
            ), recovered
            old_task_ids = {
                window.consolidation_task_id
                for window in recovered.windows
                if window.consolidation_task_id
            }
            assert old_task_ids == {queued_tasks[0]["id"]}
            old_task_id = queued_tasks[0]["id"]
            assert storage.load_shadow_task(old_task_id)["status"] == "cancelled"
            assert prep.list_prep_job_candidates(job.id) == []

            retried = prep.start_prep_job(job.id, model_id="recovery-model", fake_model=True)
            run_job(retried.id, CompletedConsolidationClient(source_file))
            completed = prep.get_prep_job(job.id)
            assert completed.status == "completed", completed
            new_task_ids = {
                window.consolidation_task_id
                for window in completed.windows
                if window.consolidation_task_id
            }
            assert len(new_task_ids) == 1
            completed_task_id = next(iter(new_task_ids))
            assert completed_task_id != old_task_id
            assert storage.load_shadow_task(old_task_id)["status"] == "cancelled"
            assert len(prep.list_prep_job_candidates(job.id)) == 1

            # A completed task can be an intermediate batch. Until the segment
            # status is durable, recovery keeps it internal and resumes from
            # the cached task rather than exposing an incomplete result.
            shadow.set_shadow_task_visibility(completed_task_id, "review")
            in_flight_windows = [
                window.model_copy(
                    update={
                        "consolidation_status": "running",
                        "consolidation_candidate_count": 0,
                        "consolidation_error": None,
                    }
                ).model_dump(mode="json")
                for window in completed.windows
            ]
            prep._save_job(completed, status="running", windows=in_flight_windows)
            interrupted_result = prep.get_prep_job(job.id)
            assert interrupted_result.status == "partial", interrupted_result
            assert all(
                window.consolidation_status == "failed" for window in interrupted_result.windows
            ), interrupted_result
            assert storage.load_shadow_task(completed_task_id)["queue_visibility"] == "internal"
            assert prep.list_prep_job_candidates(job.id) == []

            resumed = prep.start_prep_job(job.id, model_id="recovery-model", fake_model=True)
            run_job(resumed.id, NoChatClient())
            finalized = prep.get_prep_job(job.id)
            assert finalized.status == "completed", finalized
            assert finalized.candidate_count == 1
            assert storage.load_shadow_task(completed_task_id)["queue_visibility"] == "review"
            assert len(prep.list_prep_job_candidates(job.id)) == 1

            # Recovery must not replace a durable reducer failure with a
            # generic interruption message when the process stopped later.
            preserve_client = PartialMultiSegmentClient(source_file)
            preserve_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            preserve_started = prep.start_prep_job(
                preserve_job.id, model_id="recovery-model", fake_model=True
            )
            run_job(preserve_started.id, preserve_client)
            durable_failure = prep._job_from_store(preserve_job.id)
            failed_windows = [
                window
                for window in durable_failure.windows
                if window.semantic_segment_id.endswith("_2")
            ]
            original_error = failed_windows[0].consolidation_error
            assert original_error
            assert original_error != prep.INTERRUPTED_CONSOLIDATION_ERROR
            prep._save_job(durable_failure, status="running")
            preserved = prep.get_prep_job(preserve_job.id)
            preserved_failed_windows = [
                window
                for window in preserved.windows
                if window.semantic_segment_id.endswith("_2")
            ]
            assert all(window.consolidation_error == original_error for window in preserved_failed_windows)

            # A previous recovery may already have written the generic message.
            # The failed reducer run remains authoritative and must restore the
            # real error on a later interrupted-job recovery.
            generic_windows = [
                window.model_copy(
                    update={"consolidation_error": prep.INTERRUPTED_CONSOLIDATION_ERROR}
                ).model_dump(mode="json")
                for window in durable_failure.windows
            ]
            prep._save_job(durable_failure, status="partial", windows=generic_windows)
            restored = prep.get_prep_job(preserve_job.id)
            restored_failed_windows = [
                window
                for window in restored.windows
                if window.semantic_segment_id.endswith("_2")
            ]
            assert all(window.consolidation_error == original_error for window in restored_failed_windows)
            prep.delete_prep_job(preserve_job.id)

            # A segment with successful transport windows but no consolidation
            # task has not started yet; it must remain unstarted after recovery.
            unstarted_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            planned = prep._prepare_semantic_windows(
                unstarted_job, source_path, PartialMultiSegmentClient(source_file)
            )
            completed_windows = [
                window.model_copy(update={"status": "succeeded", "candidate_count": 1}).model_dump(
                    mode="json"
                )
                for window in planned.windows
            ]
            prep._save_job(planned, status="running", windows=completed_windows)
            unstarted = prep.get_prep_job(unstarted_job.id)
            assert unstarted.status == "partial"
            assert all(window.consolidation_status is None for window in unstarted.windows)
            assert all(window.consolidation_error is None for window in unstarted.windows)
            legacy_generic_windows = [
                window.model_copy(
                    update={
                        "consolidation_status": "failed",
                        "consolidation_error": prep.INTERRUPTED_CONSOLIDATION_ERROR,
                    }
                ).model_dump(mode="json")
                for window in unstarted.windows
            ]
            prep._save_job(unstarted, status="partial", windows=legacy_generic_windows)
            repaired_unstarted = prep.get_prep_job(unstarted_job.id)
            assert all(window.consolidation_status is None for window in repaired_unstarted.windows)
            assert all(window.consolidation_error is None for window in repaired_unstarted.windows)
            prep.delete_prep_job(unstarted_job.id)

            multi_client = PartialMultiSegmentClient(source_file)
            multi_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="1-3",
                    profile_id="cthulhu-dark-2e",
                )
            )
            multi_started = prep.start_prep_job(
                multi_job.id, model_id="recovery-model", fake_model=True
            )
            run_job(multi_started.id, multi_client)
            multi_partial = prep.get_prep_job(multi_job.id)
            assert multi_partial.status == "partial", multi_partial
            assert len(prep.list_prep_job_candidates(multi_job.id)) == 0
            first_segment_task = {
                window.consolidation_task_id
                for window in multi_partial.windows
                if window.semantic_segment_id.endswith("_1") and window.consolidation_task_id
            }
            assert len(first_segment_task) == 1
            calls_before_retry = multi_client.consolidation_calls
            multi_client.retrying = True
            multi_retried = prep.start_prep_job(
                multi_job.id, model_id="new-recovery-model", fake_model=True
            )
            run_job(multi_retried.id, multi_client)
            multi_completed = prep.get_prep_job(multi_job.id)
            assert multi_completed.status == "completed", multi_completed
            assert multi_client.consolidation_calls == calls_before_retry + 1
            assert len(prep.list_prep_job_candidates(multi_job.id)) == 2
            prep.delete_prep_job(multi_job.id)
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            prep.MAX_WINDOW_INPUT_CHARS = original_window_limit
            storage.init_db()

    print("PASS: interrupted semantic consolidation recovers as a retryable task")


if __name__ == "__main__":
    main()
