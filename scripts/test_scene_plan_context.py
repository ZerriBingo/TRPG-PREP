"""Regression checks for task-owned, input-free scene plan drafting."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import fitz
import httpx


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "domain"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep, storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import ExampleBundle, PrepJobCreate, load_json  # noqa: E402


def make_source_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        for page_number in range(1, 6):
            page = document.new_page()
            page.insert_text((72, 72), f"Scene source page {page_number}.")
        document.save(path)
    finally:
        document.close()


async def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    data_root = ROOT / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_root) as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / "scene-plan-source.pdf"
        make_source_pdf(source_path)
        storage.DB_PATH = temp_path / "scene-plan-context.db"
        storage.UPLOAD_DIR = temp_path / "uploads"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-scene-context",
                    "fake": True,
                }
            )
            source_file = source_path.relative_to(ROOT).as_posix()
            job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="2-4",
                    profile_id="cthulhu-dark-2e",
                    session_minutes=90,
                )
            )
            seed = ExampleBundle.model_validate(
                load_json(DOMAIN / "examples" / "naimen_pilot.json")
            )
            workspace = seed.model_copy(
                deep=True,
                update={
                    "id": job.id,
                    "name": "Task-owned scene workspace",
                    "plans": [],
                },
            )
            storage.save_domain_bundle(
                workspace.id, workspace.model_dump(mode="json", by_alias=True)
            )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                loaded = await client.get(
                    "/api/domain/workbench", params={"example": workspace.id}
                )
                assert loaded.status_code == 200, loaded.text
                context = loaded.json()["prep_context"]
                assert context["source_file"] == source_file
                assert context["page_spans"] == [{"start": 2, "end": 4, "label": None}]
                assert context["profile_id"] == "cthulhu-dark-2e"
                assert context["session_minutes"] == 90

                drafted = await client.post(
                    f"/api/domain/examples/{workspace.id}/plans/draft",
                    json={
                        "profile_id": "daggerheart",
                        "card_ids": ["card_not_real"],
                        "title": "client supplied title must be ignored",
                        "source_file": "manual://wrong",
                        "source_pages": [999],
                        "premise": "client supplied premise must be ignored",
                    },
                )
                assert drafted.status_code == 200, drafted.text
                plan = drafted.json()["plan"]
                assert plan["profile_id"] == "cthulhu-dark-2e"
                assert plan["source_file"] == source_file
                assert plan["source_pages"] == [2, 3, 4]
                assert plan["title"] == "Task-owned scene workspace · 90 分钟运行场景"
                assert plan["premise"] != "client supplied premise must be ignored"
                assert set(plan["card_ids"]) == {card.id for card in workspace.cards}

                repeated = await client.post(
                    f"/api/domain/examples/{workspace.id}/plans/draft"
                )
                assert repeated.status_code == 200, repeated.text
                assert repeated.json()["created"] is False
                assert repeated.json()["plan"]["id"] == plan["id"]
                reloaded = await client.get(
                    "/api/domain/workbench", params={"example": workspace.id}
                )
                assert len(reloaded.json()["bundle"]["plans"]) == 1

            empty_job = prep.create_prep_job(
                PrepJobCreate(
                    source_file=source_file,
                    page_range="2-4",
                    profile_id="cthulhu-dark-2e",
                    session_minutes=90,
                )
            )
            empty_workspace = ExampleBundle(
                id=empty_job.id,
                name="Facts only",
                description="No approved prep products yet.",
                profile_ids=["cthulhu-dark-2e"],
                facts=[],
                cards=[],
                plans=[],
            )
            storage.save_domain_bundle(
                empty_workspace.id,
                empty_workspace.model_dump(mode="json", by_alias=True),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                blocked = await client.post(
                    f"/api/domain/examples/{empty_workspace.id}/plans/draft"
                )
                assert blocked.status_code == 422, blocked.text
                assert "尚无已批准" in blocked.json()["detail"]
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
            storage.init_db()

    print("scene plan context checks passed")


if __name__ == "__main__":
    asyncio.run(main())
