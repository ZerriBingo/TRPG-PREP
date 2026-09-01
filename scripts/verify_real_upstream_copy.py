"""Run a small real-model artifact draft against an isolated database copy."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, prep, storage  # noqa: E402
from backend.domain import ExampleBundle  # noqa: E402


def find_workspace(requested: str | None) -> str:
    if requested:
        return requested
    candidates = []
    for raw, _updated_at in storage.list_domain_bundles():
        prep_job = prep.find_prep_job_by_workspace(raw["id"])
        if prep_job is not None and raw.get("facts"):
            candidates.append((len(raw["facts"]), raw["id"]))
    if not candidates:
        raise RuntimeError("no generated workspace with promoted facts was found")
    return min(candidates)[1]


def verify(workspace_id: str | None, fact_limit: int) -> dict:
    selected_workspace = find_workspace(workspace_id)
    prep_job = prep.find_prep_job_by_workspace(selected_workspace)
    if prep_job is None:
        raise RuntimeError("selected workspace has no preparation analysis")

    saved = storage.load_domain_bundle(selected_workspace)
    if saved is None:
        raise RuntimeError("selected workspace bundle is missing")
    bundle = ExampleBundle.model_validate(saved[0])
    bundle.facts = bundle.facts[:fact_limit]
    bundle.cards = []
    bundle.plans = []
    storage.save_domain_bundle(
        selected_workspace, bundle.model_dump(mode="json", by_alias=True)
    )

    config = storage.get_config()
    if config["fake"] or not config["api_key"]:
        raise RuntimeError("the copied configuration is not using a real model")
    job, _created = artifacts.create_artifact_job(
        selected_workspace,
        prep_job.scope.profile_id,
        model_id=config["model"],
        fake_model=False,
    )
    artifacts.execute_artifact_job(
        job.id,
        workspace_id=selected_workspace,
        profile_id=prep_job.scope.profile_id,
    )
    completed = artifacts.get_artifact_job(job.id)
    steps = storage.list_artifact_job_steps(job.id)
    final_bundle = ExampleBundle.model_validate(
        storage.load_domain_bundle(selected_workspace)[0]
    )
    if completed.status != "completed":
        failed_steps = [
            {
                "stage": step["stage"],
                "step_index": step["step_index"],
                "error": step.get("error"),
                "attempt_count": len(step.get("attempts") or []),
            }
            for step in steps
            if step.get("status") == "failed"
        ]
        detail = json.dumps(failed_steps, ensure_ascii=False)
        raise RuntimeError(
            f"{completed.error or 'real-model artifact draft failed'}; failed_steps={detail}"
        )
    return {
        "workspace_id": selected_workspace,
        "model": config["model"],
        "fact_count": completed.fact_count,
        "card_count": len(final_bundle.cards),
        "step_attempts": [
            {
                "stage": step["stage"],
                "status": step["status"],
                "attempt_count": len(step.get("attempts") or []),
            }
            for step in steps
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace")
    parser.add_argument("--fact-limit", type=int, default=8)
    args = parser.parse_args()
    if args.fact_limit < 1 or args.fact_limit > 20:
        raise SystemExit("--fact-limit must be between 1 and 20")

    source_db = ROOT / "data" / "app.db"
    if not source_db.is_file():
        raise SystemExit("data/app.db is missing")
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        copied_db = Path(temp_dir) / "real-upstream-copy.db"
        shutil.copy2(source_db, copied_db)
        storage.DB_PATH = copied_db
        storage.UPLOAD_DIR = ROOT / "data" / "uploads"
        try:
            storage.init_db()
            result = verify(args.workspace, args.fact_limit)
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
