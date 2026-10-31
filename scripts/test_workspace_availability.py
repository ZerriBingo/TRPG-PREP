"""Bookshelf availability must match what opening the project actually does.

The list used to validate only the stored structure while opening also applied the
current card/board contract, so a project the workbench refused still looked healthy.
These checks use real HTTP against a temporary database: normal, empty, pending-review,
legacy-card-type and structurally broken projects, plus the seed access boundary.
"""
from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.main import WORKSPACE_ERROR_KINDS, app  # noqa: E402
from backend.domain import DerivedCard  # noqa: E402

FIXTURE = ROOT / "backend" / "domain" / "examples" / "evidence_status_fixture.json"
SEED_ID = "evidence_status_fixture"


def project(project_id: str, name: str, cards: list[dict]) -> dict:
    return {
        "id": project_id,
        "name": name,
        "description": "",
        "profile_ids": ["cthulhu-dark-2e"],
        "facts": [],
        "cards": cards,
        "plans": [],
        "prep_selections": {},
    }


def approved_location(card_id: str) -> dict:
    return DerivedCard(
        id=card_id,
        profile_id="cthulhu-dark-2e",
        type="location",
        title="可用地点",
        fields={"normal_state": "大门锁着。", "arrival_description": "门口有人在等。"},
        edit_state="approved",
    ).model_dump(mode="json")


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "workspace-availability.db"
        try:
            storage.init_db()
            projects = {
                "ws_ready": project("ws_ready", "可用项目", [approved_location("card_ready")]),
                "ws_empty": project("ws_empty", "空项目", []),
                "ws_pending": project(
                    "ws_pending",
                    "待审核项目",
                    [{
                        **approved_location("card_pending"),
                        "edit_state": "generated",
                    }],
                ),
                "ws_legacy": project(
                    "ws_legacy",
                    "旧卡型项目",
                    [{
                        "id": "card_legacy",
                        "profile_id": "cthulhu-dark-2e",
                        "type": "scene",
                        "title": "旧场景卡",
                        "fields": {"scene_summary": "旧结构"},
                        "fact_ids": [],
                        "edit_state": "approved",
                    }],
                ),
            }
            for bundle in projects.values():
                storage.save_domain_bundle(bundle["id"], bundle)
            # Structurally broken: a card missing its required model fields.
            broken = copy.deepcopy(projects["ws_ready"])
            broken["id"] = "ws_broken"
            broken["name"] = "结构损坏项目"
            broken["cards"] = [{"id": "card_broken"}]
            storage.save_domain_bundle("ws_broken", broken)
            # Structurally broken and missing its own body id: the storage row is the
            # only identity, and the record must still appear in the bookshelf.
            no_id = copy.deepcopy(projects["ws_ready"])
            del no_id["id"]
            no_id["name"] = "正文缺少 id 的项目"
            storage.save_domain_bundle("ws_missing_id", no_id)
            mismatched_id = copy.deepcopy(projects["ws_ready"])
            mismatched_id["id"] = "body_id_elsewhere"
            mismatched_id["name"] = "正文 id 与存储行不一致"
            storage.save_domain_bundle("ws_mismatched_id", mismatched_id)

            # Keep a stored copy to prove a failed open changes nothing.
            legacy_before = storage.load_domain_bundle("ws_legacy")[0]
            storage.save_session_state(
                "ws_legacy",
                {
                    "example_id": "ws_legacy",
                    "current_plan_id": None,
                    "current_beat_id": None,
                    "current_card_id": None,
                    "trigger_states": {},
                    "revealed_clue_keys": ["clue"],
                    "clock_stages": {},
                    "log": [],
                    "notes": "保留",
                },
            )
            session_before = storage.load_session_state("ws_legacy")[0]

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                listing = await client.get("/api/domain/workspaces")
                assert listing.status_code == 200, listing.text
                by_id = {item["id"]: item for item in listing.json()["workspaces"]}

                # Readable classes stay readable and unmarked.
                for project_id in ("ws_ready", "ws_empty", "ws_pending"):
                    assert project_id in by_id, project_id
                    assert by_id[project_id]["validation_error"] is None, by_id[project_id]
                    assert by_id[project_id]["validation_kind"] is None
                    opened = await client.get(f"/api/domain/workbench?example={project_id}")
                    assert opened.status_code == 200, (project_id, opened.text)

                # Refused classes are kept, marked, and open with the same category.
                expected = {
                    "ws_legacy": "contract_incompatible",
                    "ws_broken": "model_invalid",
                    "ws_missing_id": "model_invalid",
                    "ws_mismatched_id": "model_invalid",
                }
                for project_id, kind in expected.items():
                    assert project_id in by_id, (
                        project_id, "a stored project must stay listed even when its data body is damaged"
                    )
                    entry = by_id[project_id]
                    assert entry["validation_kind"] == kind, entry
                    assert entry["validation_error"] == WORKSPACE_ERROR_KINDS[kind], entry
                    assert entry["validation_detail"], entry
                    assert entry["can_delete"] is True, entry
                    assert entry["can_rename"] is False, entry
                    opened = await client.get(f"/api/domain/workbench?example={project_id}")
                    assert opened.status_code == 422, (project_id, opened.text)
                    detail = opened.json()["detail"]
                    assert detail["code"] == kind, detail
                    assert detail["message"] == WORKSPACE_ERROR_KINDS[kind], detail
                    assert detail["detail"], detail

                # A refused open leaves the stored project and session untouched.
                assert storage.load_domain_bundle("ws_legacy")[0] == legacy_before
                assert storage.load_session_state("ws_legacy")[0] == session_before

                # Seeds stay out of the bookshelf but remain addressable by direct URL.
                assert SEED_ID not in by_id
                seed_open = await client.get(f"/api/domain/workbench?example={SEED_ID}")
                assert seed_open.status_code == 200, seed_open.text

                # An unknown project is a different, unaffected failure.
                missing = await client.get("/api/domain/workbench?example=ws_absent")
                assert missing.status_code == 404, missing.text
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: the bookshelf and the workbench judge projects by the same contract")


if __name__ == "__main__":
    asyncio.run(main())
