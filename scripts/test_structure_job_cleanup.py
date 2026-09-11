"""Deleting a workspace must remove its structure jobs and the steps they recorded."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage, structure  # noqa: E402


def table_count(query: str, params: tuple) -> int:
    conn = sqlite3.connect(storage.DB_PATH)
    try:
        row = conn.execute(query, params).fetchone()
    finally:
        conn.close()
    return int(row[0])


def step_count(job_id: str) -> int:
    return table_count("SELECT COUNT(*) FROM artifact_job_steps WHERE job_id = ?", (job_id,))


def structure_job_count(workspace_id: str) -> int:
    return table_count("SELECT COUNT(*) FROM structure_jobs WHERE workspace_id = ?", (workspace_id,))


def seed_structure_run(workspace_id: str) -> str:
    """Create one structure job plus the resumable step row it would record."""
    job, created = structure.create_structure_job(
        workspace_id, "daggerheart", model_id="scripted", fake_model=True,
    )
    assert created
    assert structure_job_count(workspace_id) == 1
    timestamp = storage.now()
    storage.save_artifact_job_step({
        "id": f"{job.id}:structure_local:1",
        "job_id": job.id,
        "stage": "structure_local",
        "step_index": 1,
        "input_hash": "fixture",
        "status": "succeeded",
        "attempts": [{"response": {"observations": []}}],
        "created_at": timestamp,
        "updated_at": timestamp,
    })
    assert step_count(job.id) == 1
    return job.id


def main() -> None:
    with tempfile.TemporaryDirectory() as directory, patch.object(
        storage, "DB_PATH", Path(directory) / "structure-cleanup.db"
    ):
        storage.init_db()
        deleted_job = seed_structure_run("workspace_single")
        assert storage.delete_domain_workspace("workspace_single")
        assert storage.load_structure_job(deleted_job) is None
        assert step_count(deleted_job) == 0
        assert structure_job_count("workspace_single") == 0

        bulk_job = seed_structure_run("workspace_bulk")
        assert storage.delete_workspace_instances(["workspace_bulk"]) == 1
        assert storage.load_structure_job(bulk_job) is None
        assert step_count(bulk_job) == 0
        assert structure_job_count("workspace_bulk") == 0

        unrelated_job = seed_structure_run("workspace_kept")
        assert storage.delete_workspace_instances(["workspace_bulk"]) == 0
        assert storage.load_structure_job(unrelated_job) is not None
        assert step_count(unrelated_job) == 1

    print("PASS: workspace deletion removes structure jobs and their recorded steps")


if __name__ == "__main__":
    main()
