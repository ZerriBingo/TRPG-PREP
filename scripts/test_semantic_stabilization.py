"""Regression contracts for the semantic-v2 stabilization pass."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

import httpx
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import PrepJobCreate  # noqa: E402


def make_source(path: Path, *, pages: int = 6, lines: int = 24, width: int = 72) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, pages + 1):
            page = document.new_page()
            for line_number in range(lines):
                text = (
                    f"PAGE-{page_number}-LINE-{line_number}-BEGIN "
                    + (f"source detail {page_number}-{line_number} " * 5)[:width]
                    + f" END-{page_number}-{line_number}"
                )
                page.insert_text((42, 42 + line_number * 10), text, fontsize=7)
        document.save(path)
    finally:
        document.close()


def _json_line(content: str, marker: str, default):
    match = re.search(rf"^{re.escape(marker)}(.+)$", content, re.MULTILINE)
    return json.loads(match.group(1)) if match else default


class PlannerFailureClient:
    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            raise RuntimeError("planner unavailable")
        return '{"candidates": []}'


class RetryClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.consolidation_calls = 0

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps({"segments": [{"start": 1, "end": 6, "label": "同一地点"}]})
        if "[TASK:prep:consolidate]" in content:
            self.consolidation_calls += 1
            if self.consolidation_calls == 1:
                return '{"candidates": ['
            pages = _json_line(content, "SEMANTIC_SEGMENT_PAGES_JSON=", [1])
            return json.dumps(
                {
                    "candidates": [
                        {
                            "text": "归并后的一条完整地点事实。",
                            "kind": "location",
                            "source_refs": [
                                {"file": self.source_file, "page": pages[0]}
                            ],
                            "confidence": 0.8,
                            "possible_links": [],
                            "open_questions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        pages = _json_line(content, "SOURCE_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": "窗口观察。",
                        "kind": "location",
                        "source_refs": [{"file": self.source_file, "page": pages[0]}],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            },
            ensure_ascii=False,
        )


class DenseConsolidationClient:
    def __init__(self) -> None:
        self.consolidation_inputs: list[int] = []

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps({"segments": [{"start": 1, "end": 6, "label": "密集地点"}]})
        if "[TASK:prep:consolidate]" in content:
            raw = _json_line(content, "WINDOW_CANDIDATES_JSON=", [])
            self.consolidation_inputs.append(len(content))
            if not raw:
                return '{"candidates": []}'
            first_ref = (raw[0].get("source_refs") or [{}])[0]
            return json.dumps(
                {
                    "candidates": [
                        {
                            "text": f"密集归并批次 {len(raw)} 条观察。",
                            "kind": "location",
                            "source_refs": [first_ref],
                            "confidence": 0.7,
                            "possible_links": [],
                            "open_questions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        source_file = _json_line(content, "SOURCE_FILE_JSON=", "fixture.pdf")
        pages = _json_line(content, "SOURCE_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": "x" * 1200,
                        "kind": "location",
                        "source_refs": [{"file": source_file, "page": pages[0]}],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            },
            ensure_ascii=False,
        )


class PartialSegmentClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {
                    "segments": [
                        {"start": 1, "end": 3, "label": "第一语义段"},
                        {"start": 4, "end": 6, "label": "第二语义段"},
                    ]
                },
                ensure_ascii=False,
            )
        if "[TASK:prep:consolidate]" in content:
            pages = _json_line(content, "SEMANTIC_SEGMENT_PAGES_JSON=", [1])
            if pages[0] >= 4:
                return "not-json"
            return json.dumps(
                {
                    "candidates": [
                        {
                            "text": "第一语义段归并结果。",
                            "kind": "location",
                            "source_refs": [
                                {"file": self.source_file, "page": pages[0]}
                            ],
                            "confidence": 0.8,
                            "possible_links": [],
                            "open_questions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        source_pages = _json_line(content, "SOURCE_PAGES_JSON=", [1])
        return json.dumps(
            {
                "candidates": [
                    {
                        "text": f"窗口观察 p{source_pages[0]}。",
                        "kind": "location",
                        "source_refs": [
                            {"file": self.source_file, "page": source_pages[0]}
                        ],
                        "confidence": 0.5,
                        "possible_links": [],
                        "open_questions": [],
                    }
                ]
            },
            ensure_ascii=False,
        )


async def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    original_limit = prep.MAX_WINDOW_INPUT_CHARS
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "semantic-stabilization.pdf"
        make_source(source_path, pages=6, lines=24)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "semantic-stabilization.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "stabilization-model"})

            prep.MAX_WINDOW_INPUT_CHARS = 520
            fallback_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            fallback = prep._prepare_semantic_windows(
                fallback_job, source_path, PlannerFailureClient()
            )
            assert fallback.segmentation_status == "fallback"
            assert fallback.semantic_segments == []
            source_pages = prep._page_texts(source_path, fallback_job.scope.page_spans)
            for page, text in source_pages.items():
                slices = [
                    text_slice
                    for window in fallback.windows
                    for text_slice in window.core_text_slices
                    if text_slice.page == page
                ]
                assert "".join(text[item.start_char : item.end_char] for item in slices) == text
                for window in fallback.windows:
                    if any(item.page == page for item in window.core_text_slices):
                        excerpt, truncated_pages = prep._window_excerpt(source_path, window)
                        assert truncated_pages == []
                        assert len(excerpt) <= prep.MAX_WINDOW_INPUT_CHARS

            prep.MAX_WINDOW_INPUT_CHARS = 900
            retry_client = RetryClient(relative_source)
            retry_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(retry_job.id, model_id="model-old", fake_model=True)
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: retry_client
            try:
                prep.execute_prep_job(started.id)
                failed = prep.get_prep_job(retry_job.id)
                assert prep.list_prep_job_candidates(retry_job.id) == []
                old_ids = {
                    window.consolidation_task_id
                    for window in failed.windows
                    if window.consolidation_task_id
                }
                assert len(old_ids) == 1
                old_id = next(iter(old_ids))
                assert storage.load_shadow_task(old_id)["queue_visibility"] == "internal"
                assert all(window.consolidation_status == "failed" for window in failed.windows)

                retried = prep.start_prep_job(
                    retry_job.id, model_id="model-new", fake_model=True
                )
                prep.execute_prep_job(retried.id)
                completed = prep.get_prep_job(retry_job.id)
                assert completed.status == "completed", completed
                new_ids = {
                    window.consolidation_task_id
                    for window in completed.windows
                    if window.consolidation_task_id
                }
                assert len(new_ids) == 1
                new_id = next(iter(new_ids))
                assert new_id != old_id
                assert storage.load_shadow_task(old_id)["queue_visibility"] == "internal"
                assert storage.load_shadow_task(new_id)["queue_visibility"] == "review"
                assert len(prep.list_prep_job_candidates(retry_job.id)) == 1

                candidate = prep.list_prep_job_candidates(retry_job.id)[0]
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    reviewed = await client.post(
                        f"/api/domain/prep/jobs/{retry_job.id}/candidates/review",
                        json={
                            "candidate_ids": [candidate["id"]],
                            "review_state": "accepted",
                        },
                    )
                    assert reviewed.status_code == 200, reviewed.text
                    assert len(reviewed.json()["promotions"]) == 1
                    workspace = await client.get(
                        f"/api/domain/workbench?example={retry_job.id}"
                    )
                    assert workspace.status_code == 200
                    assert len(workspace.json()["bundle"]["facts"]) == 1

                    deleted = await client.delete(
                        f"/api/domain/prep/jobs/{retry_job.id}"
                    )
                    assert deleted.status_code == 200, deleted.text
                assert storage.load_shadow_task(new_id) is None
                assert storage.load_domain_bundle(retry_job.id) is not None
            finally:
                prep.make_client = original_make_client

            dense_client = DenseConsolidationClient()
            dense_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            dense_started = prep.start_prep_job(
                dense_job.id, model_id="dense-model", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: dense_client
            try:
                prep.execute_prep_job(dense_started.id)
            finally:
                prep.make_client = original_make_client
            dense_completed = prep.get_prep_job(dense_job.id)
            assert dense_completed.status == "completed", dense_completed
            assert len(dense_client.consolidation_inputs) >= 2
            assert len(prep.list_prep_job_candidates(dense_job.id)) == 1
            all_tasks = storage.list_shadow_tasks()
            dense_task_ids = {
                window.consolidation_task_id
                for window in dense_completed.windows
                if window.consolidation_task_id
            }
            assert len(dense_task_ids) == 1
            assert all(
                task["queue_visibility"] == "review"
                if task["id"] in dense_task_ids
                else task["queue_visibility"] == "internal"
                for task in all_tasks
                if task["idempotency_key"].startswith(dense_job.id + ":")
            )
            dense_owned_tasks = [
                task
                for task in all_tasks
                if task["idempotency_key"].startswith(dense_job.id + ":")
            ]
            assert len(dense_owned_tasks) > len(dense_task_ids)
            assert all(
                len(task["input_excerpt"]) <= prep.MAX_CONSOLIDATION_INPUT_CHARS
                for task in dense_owned_tasks
                if task["task_kind"] == "semantic_consolidation"
            )
            prep.delete_prep_job(dense_job.id)
            assert not [
                task
                for task in storage.list_shadow_tasks()
                if task["idempotency_key"].startswith(dense_job.id + ":")
            ]

            partial_source_path = temp_path / "partial-segments.pdf"
            make_source(partial_source_path, pages=6, lines=4, width=20)
            partial_relative_source = partial_source_path.relative_to(ROOT).as_posix()
            prep.MAX_WINDOW_INPUT_CHARS = 4000
            partial_client = PartialSegmentClient(partial_relative_source)
            partial_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=partial_relative_source,
                    page_range="1-6",
                    profile_id="cthulhu-dark-2e",
                )
            )
            partial_started = prep.start_prep_job(
                partial_job.id, model_id="partial-model", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: partial_client
            try:
                prep.execute_prep_job(partial_started.id)
            finally:
                prep.make_client = original_make_client
            partial_completed = prep.get_prep_job(partial_job.id)
            assert partial_completed.status == "partial", partial_completed
            assert partial_completed.segmentation_status == "succeeded"
            assert all(window.status == "succeeded" for window in partial_completed.windows)
            grouped_statuses = {
                segment_id: {
                    window.consolidation_status
                    for window in partial_completed.windows
                    if window.semantic_segment_id == segment_id
                }
                for segment_id, _windows in prep._semantic_window_groups(partial_completed)
            }
            assert {
                frozenset(statuses) for statuses in grouped_statuses.values()
            } == {frozenset({"succeeded"}), frozenset({"failed"})}
            assert prep.list_prep_job_candidates(partial_job.id) == []
            partial_owned_tasks = [
                task
                for task in storage.list_shadow_tasks()
                if task["idempotency_key"].startswith(partial_job.id + ":")
            ]
            assert partial_owned_tasks
            assert all(task["queue_visibility"] == "internal" for task in partial_owned_tasks)
            prep.delete_prep_job(partial_job.id)
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            prep.MAX_WINDOW_INPUT_CHARS = original_limit
            storage.init_db()

    print("PASS: semantic-v2 fallback, consolidation retry, hierarchy, review, and cleanup")


if __name__ == "__main__":
    asyncio.run(main())
