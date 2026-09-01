"""Public contract checks for the fresh-project artifact workflow."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef  # noqa: E402


def bundle() -> ExampleBundle:
    return ExampleBundle(
        id="fresh_contract_test",
        name="Fresh contract test",
        description="No prep task means no generation scope.",
        profile_ids=["cthulhu-dark-2e"],
        facts=[
            SourceFact(
                id="fact_contract_location",
                source_refs=[SourceRef(file="fixture://contract", page=1)],
                evidence_status="source_fact",
                text="A source-bound location.",
                kind="location",
                visibility="explicit",
            )
        ],
        cards=[],
        plans=[],
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "fresh-contract.db"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-contract",
                    "fake": True,
                }
            )
            saved = bundle()
            storage.save_domain_bundle(saved.id, saved.model_dump(mode="json"))
            storage.create_artifact_job(
                {
                    "id": "artifact_job_legacy_contract",
                    "workspace_id": saved.id,
                    "profile_id": "cthulhu-dark-2e",
                    "model_id": "legacy-model",
                    "fake_model": True,
                    "status": "failed",
                    "phase": "completed",
                    "fact_ids": ["fact_contract_location"],
                    "created_at": storage.now(),
                    "updated_at": storage.now(),
                }
            )
            assert artifacts.list_artifact_jobs(saved.id) == []
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                empty = await client.get("/api/domain/workbench")
                assert empty.status_code == 200, empty.text
                assert empty.json()["bundle"] is None, empty.text

                for path in (
                    f"/api/domain/examples/{saved.id}/cards/draft-missing-locations",
                    f"/api/domain/examples/{saved.id}/cards/draft-missing-handouts",
                ):
                    retired = await client.post(path)
                    assert retired.status_code in {404, 405}, retired.text

                preview = await client.get(
                    "/api/domain/source-page",
                    params={"file": "fixture://contract", "page": 1},
                )
                assert preview.status_code == 404, preview.text

                draft = await client.post(
                    f"/api/domain/examples/{saved.id}/cards/draft"
                )
                assert draft.status_code == 422, draft.text
                assert "明确" in draft.json()["detail"], draft.text
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: fresh-project artifact contract")


if __name__ == "__main__":
    asyncio.run(main())
