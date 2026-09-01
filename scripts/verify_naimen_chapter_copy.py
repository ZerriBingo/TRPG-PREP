"""Verify the real 284-candidate chapter shape against an isolated DB copy."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, prep, storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import ExampleBundle  # noqa: E402


def find_workspace(requested: str | None) -> str:
    if requested:
        return requested
    candidates = []
    for raw, _updated_at in storage.list_domain_bundles():
        facts = raw.get("facts") or []
        if len(facts) >= 200:
            candidates.append((len(facts), raw["id"]))
    if not candidates:
        raise RuntimeError("no chapter-sized workspace found in the copied database")
    return max(candidates)[1]


async def verify(workspace_id: str | None) -> dict:
    selected_workspace = find_workspace(workspace_id)
    prep_job = prep.find_prep_job_by_workspace(selected_workspace)
    if prep_job is None:
        raise RuntimeError("chapter workspace has no preparation analysis")
    candidates = prep.list_prep_job_candidates(prep_job.id, review_state=None)
    candidate_ids = [item["id"] for item in candidates]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        reviewed = await client.post(
            f"/api/domain/prep/jobs/{prep_job.id}/candidates/review",
            json={"candidate_ids": candidate_ids, "review_state": "accepted"},
        )
        if reviewed.status_code != 200:
            raise RuntimeError(f"chapter batch review failed: {reviewed.text}")

    saved = storage.load_domain_bundle(selected_workspace)
    if saved is None:
        raise RuntimeError("chapter workspace bundle is missing")
    bundle = ExampleBundle.model_validate(saved[0])
    bundle.cards = []
    bundle.plans = []
    storage.save_domain_bundle(
        selected_workspace, bundle.model_dump(mode="json", by_alias=True)
    )
    storage.set_config(
        {
            "base_url": "http://offline.invalid",
            "api_key": "",
            "model": "fake-real-chapter-shape",
            "fake": True,
        }
    )
    job, _created = artifacts.create_artifact_job(
        selected_workspace,
        prep_job.scope.profile_id,
        model_id="fake-real-chapter-shape",
        fake_model=True,
    )
    artifacts.execute_artifact_job(
        job.id,
        workspace_id=selected_workspace,
        profile_id=prep_job.scope.profile_id,
    )
    completed = artifacts.get_artifact_job(job.id)
    steps = storage.list_artifact_job_steps(job.id)
    final_bundle = ExampleBundle.model_validate(storage.load_domain_bundle(selected_workspace)[0])
    stages = [step["stage"] for step in steps if step["status"] == "succeeded"]
    if completed.status != "completed":
        raise RuntimeError(completed.error or "chapter hierarchy did not complete")
    if "global_plan" not in stages or "materialize" not in stages:
        raise RuntimeError(f"missing hierarchical stages: {stages}")
    return {
        "workspace_id": selected_workspace,
        "candidate_count": len(candidate_ids),
        "reviewed_count": len(reviewed.json()["candidates"]),
        "fact_count": completed.fact_count,
        "local_batch_count": completed.batch_count,
        "local_unit_count": completed.unit_count,
        "planned_card_count": completed.planned_card_count,
        "card_count": len(final_bundle.cards),
        "succeeded_step_count": len(stages),
        "stages": sorted(set(stages)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace")
    args = parser.parse_args()
    source_db = ROOT / "data" / "app.db"
    if not source_db.is_file():
        raise SystemExit("data/app.db is missing")

    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        copied_db = Path(temp_dir) / "chapter-copy.db"
        shutil.copy2(source_db, copied_db)
        storage.DB_PATH = copied_db
        storage.UPLOAD_DIR = ROOT / "data" / "uploads"
        try:
            storage.init_db()
            result = asyncio.run(verify(args.workspace))
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
