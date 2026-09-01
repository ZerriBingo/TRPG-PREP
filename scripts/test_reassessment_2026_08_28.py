"""Regression checks for the 2026-08-28 project-direction decisions."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import fitz
import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, prep, storage  # noqa: E402
from backend.app.llm import FakeLLM, find_task  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    ExtractionWindow,
    PageSpan,
    PrepJob,
    PrepJobCreate,
    PrepScope,
    SourceFact,
    SourceRef,
)


def make_running_header_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 11):
            page = document.new_page()
            page.insert_text(
                (72, 72),
                "CHAPTER ONE\n"
                f"Page {page_number}. This location description continues across pages "
                "and the repeated line above is a running header, not a scene boundary.",
            )
        document.save(path)
    finally:
        document.close()


def large_workspace() -> ExampleBundle:
    facts = [
        SourceFact(
            id=f"fact_hierarchy_{index:03d}",
            source_refs=[SourceRef(file="fixture://chapter", page=(index % 85) + 96)],
            evidence_status="source_fact",
            text=(
                f"Chapter fact {index}: a source-bound person, place, clue, threat, or event. "
                * 22
            ),
            kind=("npc", "location", "clue", "threat", "event")[index % 5],
            visibility="explicit",
        )
        for index in range(284)
    ]
    return ExampleBundle(
        id="reassessment_large_workspace",
        name="第一章",
        description="large hierarchy fixture",
        profile_ids=["cthulhu-dark-2e"],
        facts=facts,
        cards=[],
        plans=[],
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        temp_path = Path(temp_dir)
        storage.DB_PATH = temp_path / "reassessment.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-reassessment",
                    "fake": True,
                }
            )

            source_path = temp_path / "running-header.pdf"
            make_running_header_pdf(source_path)
            relative_source = source_path.relative_to(ROOT).as_posix()
            windows = prep._build_windows(source_path, [PageSpan(start=1, end=10)], "decision")
            assert [(item.core_span.start, item.core_span.end) for item in windows] == [
                (1, 3),
                (4, 6),
                (7, 9),
                (10, 10),
            ]
            assert all(
                item.boundary_basis in {"page_limit", "char_budget", "scope_end"}
                for item in windows
            ), [item.boundary_basis for item in windows]

            first = prep.create_prep_job(
                PrepJobCreate(
                    source_file=relative_source,
                    page_range="1-10",
                    profile_id="cthulhu-dark-2e",
                )
            )
            rebuilt = prep.rebuild_prep_job(first.id)
            assert first.window_strategy == "core-context-v3"
            assert rebuilt.workspace_id == first.workspace_id
            assert rebuilt.analysis_version == 2
            assert rebuilt.previous_job_id == first.id
            assert prep.find_prep_job_by_workspace(first.workspace_id).id == rebuilt.id

            workspace = large_workspace()
            storage.save_domain_bundle(workspace.id, workspace.model_dump(mode="json"))
            artifact_scope = PrepJob(
                id="prep_job_reassessment_large",
                status="completed",
                scope=PrepScope(
                    source_file="fixture://chapter",
                    source_version="v1",
                    page_spans=[PageSpan(start=96, end=180)],
                    profile_id="cthulhu-dark-2e",
                    objective="chapter artifact contract",
                ),
                model_id="fake-reassessment",
                prompt_version="test",
                schema_version="test",
                workspace_id=workspace.id,
                windows=[
                    ExtractionWindow(
                        id="prep_window_reassessment_large",
                        page_span=PageSpan(start=96, end=180),
                        core_span=PageSpan(start=96, end=180),
                        status="succeeded",
                        input_chars=10,
                    )
                ],
                created_at=storage.now(),
                updated_at=storage.now(),
            )
            storage.create_prep_job(artifact_scope.model_dump(mode="json"))

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                drafted = await client.post(
                    f"/api/domain/examples/{workspace.id}/cards/draft"
                )
                assert drafted.status_code == 202, drafted.text
                artifact_job_id = drafted.json()["job"]["id"]
                artifact_job = drafted.json()["job"]
                for _ in range(300):
                    current = await client.get(
                        f"/api/domain/examples/{workspace.id}/cards/draft-jobs/{artifact_job_id}"
                    )
                    assert current.status_code == 200, current.text
                    artifact_job = current.json()["job"]
                    if artifact_job["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.01)

                assert artifact_job["status"] == "completed", artifact_job
                assert artifact_job["fact_count"] == 284
                assert artifact_job["batch_count"] > 1
                assert artifact_job["completed_batches"] == artifact_job["batch_count"]
                assert artifact_job["phase"] == "completed"
                assert artifact_job["unit_count"] > 0

                saved = storage.load_domain_bundle(workspace.id)
                assert saved is not None
                cards = saved[0]["cards"]
                assert cards
                cited_pages = {
                    ref["page"]
                    for fact in saved[0]["facts"]
                    if any(fact["id"] in card["fact_ids"] for card in cards)
                    for ref in fact["source_refs"]
                }
                assert min(cited_pages) < 120
                assert max(cited_pages) > 150

                retry_workspace = large_workspace().model_copy(
                    update={"id": "reassessment_retry_workspace", "name": "重试验证"}
                )
                storage.save_domain_bundle(
                    retry_workspace.id, retry_workspace.model_dump(mode="json")
                )
                retry_scope = artifact_scope.model_copy(update={
                    "id": "prep_job_reassessment_retry",
                    "workspace_id": retry_workspace.id,
                    "updated_at": storage.now(),
                })
                storage.create_prep_job(retry_scope.model_dump(mode="json"))
                storage.set_config(
                    {
                        "base_url": "http://offline.invalid",
                        "api_key": "",
                        "model": "fake-reassessment-retry",
                        "fake": True,
                    }
                )

                class FlakyFakeClient:
                    def __init__(self) -> None:
                        self.inner = FakeLLM({"model": "fake-reassessment-retry"})
                        self.model = self.inner.model
                        self.calls: dict[str, int] = {}
                        self.failed_once = False

                    def chat_json(self, messages, **kwargs):
                        task = find_task(messages)
                        self.calls[task] = self.calls.get(task, 0) + 1
                        if task == "prep:artifact_materialize" and not self.failed_once:
                            self.failed_once = True
                            raise RuntimeError("forced one-time materialization failure")
                        return self.inner.chat_json(messages, **kwargs)

                flaky = FlakyFakeClient()
                original_make_client = artifacts.make_client
                artifacts.make_client = lambda **_kwargs: flaky
                try:
                    first_try = await client.post(
                        f"/api/domain/examples/{retry_workspace.id}/cards/draft"
                    )
                    assert first_try.status_code == 202, first_try.text
                    retry_job_id = first_try.json()["job"]["id"]
                    first_status = await client.get(
                        f"/api/domain/examples/{retry_workspace.id}/cards/draft-jobs/{retry_job_id}"
                    )
                    failed_job = first_status.json()["job"]
                    assert failed_job["status"] == "failed"
                    assert failed_job["completed_cards"] == failed_job["planned_card_count"]
                    assert failed_job["card_count"] < failed_job["planned_card_count"]
                    assert "已全部尝试" in failed_job["error"]
                    local_calls = flaky.calls.get("prep:artifact_local_digest", 0)
                    global_calls = flaky.calls.get("prep:artifact_global_plan", 0)
                    materialize_calls = flaky.calls.get("prep:artifact_materialize", 0)
                    assert materialize_calls >= failed_job["planned_card_count"]

                    duplicate_try = await client.post(
                        f"/api/domain/examples/{retry_workspace.id}/cards/draft"
                    )
                    assert duplicate_try.status_code == 202, duplicate_try.text
                    assert duplicate_try.json()["created"] is False
                    assert duplicate_try.json()["job"]["id"] == retry_job_id

                    second_try = await client.post(
                        f"/api/domain/examples/{retry_workspace.id}/cards/draft-jobs/{retry_job_id}/retry"
                    )
                    assert second_try.status_code == 202, second_try.text
                    assert second_try.json()["job"]["id"] == retry_job_id
                    second_status = await client.get(
                        f"/api/domain/examples/{retry_workspace.id}/cards/draft-jobs/{retry_job_id}"
                    )
                    assert second_status.json()["job"]["status"] == "completed", second_status.text
                    assert flaky.calls.get("prep:artifact_local_digest", 0) == local_calls
                    assert flaky.calls.get("prep:artifact_global_plan", 0) == global_calls
                    steps = storage.list_artifact_job_steps(retry_job_id)
                    failed_card_step = next(
                        step
                        for step in steps
                        if step["stage"] == "materialize" and step["step_index"] == 1
                    )
                    assert [attempt["status"] for attempt in failed_card_step["attempts"]] == [
                        "failed",
                        "succeeded",
                    ]
                finally:
                    artifacts.make_client = original_make_client
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir

    print("PASS: one project keeps analysis versions and large facts use hierarchical generation")


if __name__ == "__main__":
    asyncio.run(main())
