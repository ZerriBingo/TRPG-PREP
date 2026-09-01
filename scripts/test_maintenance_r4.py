"""R4 maintenance checks for workspace lifecycle, batching, and page windows."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import fitz
import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, prep, storage  # noqa: E402
from backend.app.main import app, remove_seed_workspace_instances  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    ExtractionWindow,
    PageSpan,
    PrepJob,
    PrepScope,
    SourceFact,
    SourceRef,
    load_profiles,
)


def make_heading_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 11):
            page = document.new_page()
            page.insert_text(
                (72, 72),
                f"CHAPTER RUNNING HEADER\nPage {page_number}. "
                "A location description continues across adjacent pages.",
            )
        document.save(path)
    finally:
        document.close()


def large_workspace() -> ExampleBundle:
    facts = [
        SourceFact(
            id=f"fact_large_{index}",
            source_refs=[SourceRef(file="fixture://large", page=(index % 20) + 1)],
            evidence_status="source_fact",
            text=("A source-bound fact with enough text to exercise batching. " * 30)
            + str(index),
            kind="event",
            visibility="explicit",
        )
        for index in range(200)
    ]
    return ExampleBundle(
        id="r4_large_workspace",
        name="p96-180",
        description="maintenance fixture",
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
        storage.DB_PATH = temp_path / "r4-maintenance.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-r4",
                    "fake": True,
                }
            )
            source_path = temp_path / "heading-source.pdf"
            make_heading_pdf(source_path)
            windows = prep._build_windows(source_path, [PageSpan(start=1, end=10)], "r4")
            assert [(item.core_span.start, item.core_span.end) for item in windows] == [
                (1, 3), (4, 6), (7, 9), (10, 10)
            ]

            bundle = large_workspace()
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json"))
            prep_job = PrepJob(
                id="prep_job_r4_large_workspace",
                status="completed",
                scope=PrepScope(
                    source_file="fixture://large",
                    source_version="v1",
                    page_spans=[PageSpan(start=1, end=20)],
                    profile_id="cthulhu-dark-2e",
                    objective="maintenance fixture",
                ),
                model_id="fake-r4",
                prompt_version="test",
                schema_version="test",
                workspace_id=bundle.id,
                windows=[
                    ExtractionWindow(
                        id="prep_window_r4_large_workspace",
                        page_span=PageSpan(start=1, end=20),
                        core_span=PageSpan(start=1, end=20),
                        status="succeeded",
                        input_chars=10,
                    )
                ],
                created_at=storage.now(),
                updated_at=storage.now(),
            )
            storage.create_prep_job(prep_job.model_dump(mode="json"))
            seed_bundle = ExampleBundle.model_validate_json(
                (ROOT / "backend" / "domain" / "examples" / "naimen_pilot.json").read_text(
                    encoding="utf-8"
                )
            )
            storage.save_domain_bundle(seed_bundle.id, seed_bundle.model_dump(mode="json"))
            assert remove_seed_workspace_instances() == 1
            assert storage.load_domain_bundle(seed_bundle.id) is None
            ordered_facts = artifacts._facts_for_workspace(bundle, bundle.id)
            assert ordered_facts[0].source_refs[0].page == 1
            assert ordered_facts[0].id == "fact_large_0"
            huge_fact = SourceFact(
                id="fact_huge_input",
                source_refs=[SourceRef(file="fixture://large", page=1)],
                evidence_status="source_fact",
                text="x" * 90_000,
                kind="event",
                visibility="explicit",
            )
            try:
                artifacts._fact_batches([huge_fact])
            except artifacts.ArtifactGenerationError as error:
                assert "单条输入" in str(error)
            else:
                raise AssertionError("oversized single fact should be rejected")
            profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
            prompt = artifacts._prompt_messages(
                bundle,
                profiles["cthulhu-dark-2e"],
                [{"id": "fact_large_0", "text": "fixture"}],
                require_runtime_anchor=False,
                batch_index=1,
                batch_count=2,
            )
            assert "transport batch 1 of 2" in prompt[-1]["content"]
            storage.save_session_state(
                bundle.id,
                {"example_id": bundle.id, "notes": "temporary", "log": []},
            )
            artifact_job = artifacts.create_artifact_job(
                bundle.id,
                "cthulhu-dark-2e",
                model_id="fake-r4",
                fake_model=True,
            )[0]
            artifact_job = artifacts._save_job(
                artifact_job, status="completed", phase="completed", error=None
            )

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                renamed = await client.patch(
                    f"/api/domain/workspaces/{bundle.id}", json={"name": "第一章"}
                )
                assert renamed.status_code == 200, renamed.text
                assert renamed.json()["workspace"]["name"] == "第一章"
                assert (
                    await client.get("/api/domain/workbench", params={"example": bundle.id})
                ).json()["bundle"]["name"] == "第一章"

                seed_rename = await client.patch(
                    "/api/domain/workspaces/naimen_pilot", json={"name": "不可改"}
                )
                assert seed_rename.status_code == 404, seed_rename.text
                seed_delete = await client.delete("/api/domain/workspaces/naimen_pilot")
                assert seed_delete.status_code == 404, seed_delete.text

                draft = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/draft"
                )
                assert draft.status_code == 202, draft.text
                job_id = draft.json()["job"]["id"]
                job = draft.json()["job"]
                for _ in range(100):
                    current = await client.get(
                        f"/api/domain/examples/{bundle.id}/cards/draft-jobs/{job_id}"
                    )
                    assert current.status_code == 200, current.text
                    job = current.json()["job"]
                    if job["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.01)
                assert job["status"] == "completed", job
                assert job["fact_count"] == 200
                assert job["batch_count"] > 1
                assert job["completed_batches"] == job["batch_count"]

                deleted = await client.delete(f"/api/domain/workspaces/{bundle.id}")
                assert deleted.status_code == 200, deleted.text
                assert storage.load_domain_bundle(bundle.id) is None
                assert storage.load_session_state(bundle.id) is None
                assert storage.load_artifact_job(artifact_job.id) is None
                assert storage.load_artifact_job(job_id) is None
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir

    print("PASS: R4 workspace lifecycle, large artifact batches, and soft page boundaries")


if __name__ == "__main__":
    asyncio.run(main())
