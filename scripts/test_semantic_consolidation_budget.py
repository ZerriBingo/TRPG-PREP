"""Regression contract for dense semantic-segment consolidation payloads."""
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
        page = document.new_page()
        page.insert_text(
            (48, 48),
            "Dense source fixture with many distinct supported observations.",
            fontsize=9,
        )
        document.save(path)
    finally:
        document.close()


class DenseSegmentClient:
    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.consolidation_inputs: list[dict] = []

    @staticmethod
    def _observation(index: int) -> dict:
        return {
            "text": (
                f"Distinct supported observation {index}: "
                + "supported source detail " * 12
            ),
            "kind": "clue",
            "source_refs": [{"file": None, "page": 1}],
            "confidence": 0.5,
            "possible_links": [],
            "open_questions": [],
        }

    def chat(self, messages, **_kwargs):
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if "[TASK:prep:segment]" in content:
            return json.dumps(
                {"segments": [{"start": 1, "end": 1, "label": "资料索引"}]},
                ensure_ascii=False,
            )
        if "[TASK:prep:consolidate]" in content:
            raw_candidates = json.loads(
                content.split("WINDOW_CANDIDATES_JSON=", 1)[1].splitlines()[0]
            )
            self.consolidation_inputs.append(raw_candidates)
            candidates = []
            for raw in raw_candidates:
                refs = []
                for reference in raw["source_refs"]:
                    item = {"file": self.source_file, "page": reference["page"]}
                    if reference.get("locator") is not None:
                        item["locator"] = reference["locator"]
                    refs.append(item)
                candidates.append(
                    {
                        "text": raw["text"],
                        "kind": raw["kind"],
                        "source_refs": refs,
                        "confidence": 0.5,
                        "possible_links": raw.get("possible_links", []),
                        "open_questions": raw.get("open_questions", []),
                    }
                )
            return json.dumps({"candidates": candidates}, ensure_ascii=False)

        return json.dumps(
            {
                "candidates": [
                    {
                        **self._observation(index),
                        "source_refs": [{"file": self.source_file, "page": 1}],
                    }
                    for index in range(1, 38)
                ]
            },
            ensure_ascii=False,
        )


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    original_consolidation_limit = prep.MAX_CONSOLIDATION_INPUT_CHARS
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "dense.pdf"
        make_source(source_path)
        relative_source = source_path.relative_to(ROOT).as_posix()
        storage.DB_PATH = temp_path / "dense-consolidation.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config({"fake": True, "model": "dense-consolidation-fixture"})
            client = DenseSegmentClient(relative_source)
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1",
                    profile_id="cthulhu-dark-2e",
                )
            )
            started = prep.start_prep_job(
                job.id, model_id="dense-consolidation-fixture", fake_model=True
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: client
            try:
                prep.execute_prep_job(started.id)
            finally:
                prep.make_client = original_make_client

            completed = prep.get_prep_job(job.id)
            assert completed.status == "completed", completed
            assert len(client.consolidation_inputs) == 1
            assert len(client.consolidation_inputs[0]) == 37
            assert all(
                item["candidate_id"].startswith("c")
                for item in client.consolidation_inputs[0]
            )
            assert all(
                item["source_refs"] == [{"page": 1}]
                for item in client.consolidation_inputs[0]
            )
            assert all("confidence" not in item for item in client.consolidation_inputs[0])
            candidates = prep.list_prep_job_candidates(job.id)
            assert len(candidates) == 37
            assert all(
                len(candidate["source_refs"]) == 1
                and candidate["source_refs"][0]["file"] == relative_source
                and candidate["source_refs"][0]["page"] == 1
                for candidate in candidates
            )
            consolidation_tasks = [
                task
                for task in shadow.list_shadow_tasks(include_internal=True)
                if task.task_kind == "semantic_consolidation"
            ]
            assert len(consolidation_tasks) == 1

            # The reducer must also finish when distinct, valid observations
            # exceed one request budget but cannot be merged further. This is
            # the real multi-batch failure mode: a repeated model pass echoes
            # each batch, while the complete segment result still fits the
            # review response contract.
            prep.MAX_CONSOLIDATION_INPUT_CHARS = 6000
            batched_client = DenseSegmentClient(relative_source)
            batched_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1",
                    profile_id="cthulhu-dark-2e",
                )
            )
            batched_started = prep.start_prep_job(
                batched_job.id,
                model_id="dense-consolidation-fixture",
                fake_model=True,
            )
            original_make_client = prep.make_client
            prep.make_client = lambda **_kwargs: batched_client
            try:
                prep.execute_prep_job(batched_started.id)
            finally:
                prep.make_client = original_make_client

            batched_completed = prep.get_prep_job(batched_job.id)
            assert batched_completed.status == "completed", batched_completed
            assert len(batched_client.consolidation_inputs) > 1
            batched_candidates = prep.list_prep_job_candidates(batched_job.id)
            assert len(batched_candidates) == 37
            assert all(
                candidate["candidate_role"] == "segment_result"
                for candidate in batched_candidates
            )
            batched_tasks = [
                task
                for task in shadow.list_shadow_tasks(include_internal=True)
                if task.idempotency_key.startswith(batched_job.id + ":")
                and task.task_kind == "semantic_consolidation"
            ]
            final_task_ids = {
                window.consolidation_task_id
                for window in batched_completed.windows
                if window.consolidation_task_id
            }
            assert len(final_task_ids) == 1
            final_task_id = next(iter(final_task_ids))
            final_task = next(task for task in batched_tasks if task.id == final_task_id)
            assert final_task.prompt_version == prep.DETERMINISTIC_CONSOLIDATION_VERSION
            assert final_task.queue_visibility == "review"
            assert final_task.parent_task_ids
            assert all(
                task.queue_visibility == "review"
                if task.id == final_task_id
                else task.queue_visibility == "internal"
                for task in batched_tasks
            )
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            prep.MAX_CONSOLIDATION_INPUT_CHARS = original_consolidation_limit
            storage.init_db()

    print("PASS: dense distinct observations preserve evidence across consolidation budgets")


if __name__ == "__main__":
    main()
