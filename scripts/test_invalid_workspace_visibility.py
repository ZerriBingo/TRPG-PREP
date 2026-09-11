"""Invalid downstream structures remain visible without accepting invalid data."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import storage, main  # noqa: E402
from fastapi import HTTPException  # noqa: E402

with tempfile.TemporaryDirectory() as directory, patch.object(storage, "DB_PATH", Path(directory) / "test.db"):
    storage.init_db()
    raw = {"id": "prep_job_broken", "name": "Broken structure", "facts": [],
           "story_beats": [{"id": "story_beat_old", "chapter_ids": ["chapter_a", "chapter_b"]}]}
    storage.save_domain_bundle(raw["id"], raw)
    items = main.get_domain_workspaces()["workspaces"]
    assert len(items) == 1 and items[0]["id"] == raw["id"]
    assert items[0]["validation_error"] and not items[0]["can_rename"]
    try:
        main.load_runtime_domain_example(raw["id"])
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError("invalid structure must not become runnable")
    assert storage.load_domain_bundle(raw["id"])[0] == raw
print("PASS: invalid workspace stays visible and unmodified, runtime remains strict")
