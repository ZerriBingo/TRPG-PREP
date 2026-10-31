"""Preparation-selection storage: validation, isolation, and state boundaries.

The selection is a reading aid for one board of one project. These checks pin that it
cannot smuggle in unapproved or foreign cards, that saving it never touches plans or
session state, and that a stale reference cannot make the project unreadable.
"""
from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import ExampleBundle, load_json  # noqa: E402

FIXTURE = ROOT / "backend" / "domain" / "examples" / "evidence_status_fixture.json"

DH_NPC = {
    "id": "card_test_dh_npc",
    "campaign_id": None,
    "profile_id": "daggerheart",
    "type": "npc",
    "title": "测试奇幻人物",
    "subtitle": None,
    "fact_ids": [],
    "fields": {
        "short_term_goal": "先摸清来意。",
        "long_term_goal": "保住商路。",
        "offers": "一份通行凭证。",
        "relationship_pressure": "怀疑外来者。",
        "attitude_changes": "被说动后让步。",
    },
    "field_sources": {},
    "open_questions": [],
    "runtime_group_ids": [],
    "generation": None,
    "edit_state": "approved",
}


def project_bundle(bundle_id: str, *, with_selection_field: bool = True) -> dict:
    bundle = copy.deepcopy(load_json(FIXTURE))
    bundle["id"] = bundle_id
    bundle["name"] = "准备选择测试项目"
    bundle["profile_ids"] = ["cthulhu-dark-2e", "daggerheart"]
    bundle["cards"] = [dict(card) for card in bundle["cards"]]
    bundle["plans"] = []
    approved = [card for card in bundle["cards"] if card.get("edit_state") == "approved"]
    extra_edited = copy.deepcopy(approved[0])
    extra_edited.update({"id": "card_test_edited", "title": "已修改待批准", "edit_state": "edited"})
    bundle["cards"].extend([copy.deepcopy(DH_NPC), extra_edited])
    if with_selection_field:
        bundle["prep_selections"] = {}
    return bundle


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "prep-selection.db"
        try:
            storage.init_db()
            project_a = project_bundle("prep_test_a")
            project_b = project_bundle("prep_test_b")
            legacy = project_bundle("prep_test_legacy", with_selection_field=False)
            for bundle in (project_a, project_b, legacy):
                storage.save_domain_bundle(bundle["id"], bundle)
            storage.save_session_state(
                "prep_test_a",
                {
                    "example_id": "prep_test_a",
                    "current_plan_id": None,
                    "current_beat_id": None,
                    "current_card_id": None,
                    "trigger_states": {},
                    "revealed_clue_keys": ["clue_one"],
                    "clock_stages": {},
                    "log": [],
                    "notes": "既有备注",
                },
            )

            ct_approved = next(
                card["id"] for card in project_a["cards"]
                if card.get("edit_state") == "approved" and card["profile_id"] == "cthulhu-dark-2e"
            )
            ct_generated = next(
                card["id"] for card in project_a["cards"] if card.get("edit_state") == "generated"
            )

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                saved_plan = storage.load_domain_bundle("prep_test_a")[0]["plans"]
                saved_session = storage.load_session_state("prep_test_a")[0]

                response = await client.put(
                    "/api/domain/examples/prep_test_a/prep-selection",
                    json={"profile_id": "cthulhu-dark-2e", "card_ids": [ct_approved]},
                )
                assert response.status_code == 200, response.text
                assert response.json()["card_ids"] == [ct_approved]

                stored = storage.load_domain_bundle("prep_test_a")[0]
                assert stored["prep_selections"] == {"cthulhu-dark-2e": [ct_approved]}
                assert stored["plans"] == saved_plan, "saving a selection must not rewrite plans"
                assert storage.load_session_state("prep_test_a")[0] == saved_session, (
                    "saving a selection must not rewrite session state"
                )

                # Other board in the same project is independent.
                dh = await client.put(
                    "/api/domain/examples/prep_test_a/prep-selection",
                    json={"profile_id": "daggerheart", "card_ids": ["card_test_dh_npc"]},
                )
                assert dh.status_code == 200, dh.text
                stored = storage.load_domain_bundle("prep_test_a")[0]
                assert stored["prep_selections"]["cthulhu-dark-2e"] == [ct_approved]
                assert stored["prep_selections"]["daggerheart"] == ["card_test_dh_npc"]

                # Other project keeps an empty selection.
                other_bundle = storage.load_domain_bundle("prep_test_b")[0]
                assert other_bundle.get("prep_selections", {}) == {}

                # Rejections: duplicates, unknown, unapproved, other board, unknown board.
                rejected = [
                    {"profile_id": "cthulhu-dark-2e", "card_ids": [ct_approved, ct_approved]},
                    {"profile_id": "cthulhu-dark-2e", "card_ids": ["card_missing"]},
                    {"profile_id": "cthulhu-dark-2e", "card_ids": [ct_generated]},
                    {"profile_id": "cthulhu-dark-2e", "card_ids": ["card_test_edited"]},
                    {"profile_id": "cthulhu-dark-2e", "card_ids": ["card_test_dh_npc"]},
                    {"profile_id": "daggerheart", "card_ids": [ct_approved]},
                    {"profile_id": "module-prep", "card_ids": []},
                    {"profile_id": "unknown-board", "card_ids": []},
                ]
                for body in rejected:
                    bad = await client.put(
                        "/api/domain/examples/prep_test_a/prep-selection", json=body
                    )
                    assert bad.status_code == 422, f"{body} should be rejected: {bad.text}"
                    assert bad.json().get("detail")
                stored = storage.load_domain_bundle("prep_test_a")[0]
                assert stored["prep_selections"]["cthulhu-dark-2e"] == [ct_approved], (
                    "a rejected save must leave the stored selection untouched"
                )

                # Saving an empty list only clears that board's selection.
                cleared = await client.put(
                    "/api/domain/examples/prep_test_a/prep-selection",
                    json={"profile_id": "cthulhu-dark-2e", "card_ids": []},
                )
                assert cleared.status_code == 200, cleared.text
                stored = storage.load_domain_bundle("prep_test_a")[0]
                assert stored["prep_selections"]["cthulhu-dark-2e"] == []
                assert stored["prep_selections"]["daggerheart"] == ["card_test_dh_npc"]
                assert stored["plans"] == saved_plan
                assert storage.load_session_state("prep_test_a")[0] == saved_session

                # A narrow save keeps unrelated parallel edits made after the client loaded.
                fresh = storage.load_domain_bundle("prep_test_a")[0]
                fresh["cards"][0]["title"] = "并行修改后的标题"
                storage.save_domain_bundle("prep_test_a", fresh)
                again = await client.put(
                    "/api/domain/examples/prep_test_a/prep-selection",
                    json={"profile_id": "cthulhu-dark-2e", "card_ids": [ct_approved]},
                )
                assert again.status_code == 200, again.text
                stored = storage.load_domain_bundle("prep_test_a")[0]
                assert stored["cards"][0]["title"] == "并行修改后的标题"
                assert stored["prep_selections"]["cthulhu-dark-2e"] == [ct_approved]

                # A whole-bundle save from a stale tab must not rewrite the selection.
                stale_payload = copy.deepcopy(storage.load_domain_bundle("prep_test_a")[0])
                stale_payload["prep_selections"] = {}
                whole_save = await client.put(
                    "/api/domain/examples/prep_test_a/bundle", json=stale_payload
                )
                assert whole_save.status_code == 200, whole_save.text
                assert storage.load_domain_bundle("prep_test_a")[0]["prep_selections"] == {
                    "cthulhu-dark-2e": [ct_approved],
                    "daggerheart": ["card_test_dh_npc"],
                }, "whole-bundle saves cannot change preparation selections"

                # A card that later returns to review keeps the project readable and the
                # stored reference intact; the reader decides how to present it.
                stale = storage.load_domain_bundle("prep_test_a")[0]
                for card in stale["cards"]:
                    if card["id"] == ct_approved:
                        card["edit_state"] = "edited"
                storage.save_domain_bundle("prep_test_a", stale)
                readable = await client.get("/api/domain/workbench?example=prep_test_a")
                assert readable.status_code == 200, readable.text
                payload = readable.json()
                assert payload["bundle"]["prep_selections"]["cthulhu-dark-2e"] == [ct_approved]
                assert storage.load_domain_bundle("prep_test_a")[0]["prep_selections"][
                    "cthulhu-dark-2e"
                ] == [ct_approved]

                # A project saved before this field existed stays readable and empty.
                legacy_read = await client.get("/api/domain/workbench?example=prep_test_legacy")
                assert legacy_read.status_code == 200, legacy_read.text
                assert legacy_read.json()["bundle"].get("prep_selections", {}) == {}
                legacy_save = await client.put(
                    "/api/domain/examples/prep_test_legacy/prep-selection",
                    json={"profile_id": "cthulhu-dark-2e", "card_ids": []},
                )
                assert legacy_save.status_code == 200, legacy_save.text
                assert storage.load_domain_bundle("prep_test_legacy")[0]["prep_selections"] == {
                    "cthulhu-dark-2e": []
                }

                # Another project cannot be targeted through this project's URL.
                mismatch = await client.put(
                    "/api/domain/examples/prep_test_b/prep-selection",
                    json={"profile_id": "unknown-board", "card_ids": []},
                )
                assert mismatch.status_code == 422, mismatch.text

                # Concurrent saves of two boards must both survive. The old
                # read-modify-write let the last writer revert the other board.
                for round_index in range(10):
                    horror_ids = [ct_approved]
                    fantasy_ids = ["card_test_dh_npc"]
                    await asyncio.gather(
                        client.put(
                            "/api/domain/examples/prep_test_a/prep-selection",
                            json={"profile_id": "cthulhu-dark-2e", "card_ids": horror_ids},
                        ),
                        client.put(
                            "/api/domain/examples/prep_test_a/prep-selection",
                            json={"profile_id": "daggerheart", "card_ids": fantasy_ids},
                        ),
                    )
                    selections = storage.load_domain_bundle("prep_test_a")[0]["prep_selections"]
                    assert selections.get("cthulhu-dark-2e") == horror_ids, round_index
                    assert selections.get("daggerheart") == fantasy_ids, round_index

                # A bundle write that started from an older read cannot revert a
                # selection that landed meanwhile.
                stale = copy.deepcopy(storage.load_domain_bundle("prep_test_a")[0])
                stale["prep_selections"] = {}
                storage.update_domain_bundle_prep_selection(
                    "prep_test_a", "cthulhu-dark-2e", [ct_approved]
                )
                storage.save_domain_bundle("prep_test_a", stale)
                selections = storage.load_domain_bundle("prep_test_a")[0]["prep_selections"]
                assert selections.get("cthulhu-dark-2e") == [ct_approved], (
                    "a stale whole-bundle write must not drop a stored selection"
                )
                assert selections.get("daggerheart") == ["card_test_dh_npc"]

                # Both first writes can see an unpersisted seed. Align those misses
                # so a subsequent insert cannot discard the other successful board.
                seed = project_bundle("prep_test_seed")
                barrier = threading.Barrier(2, timeout=10)
                original_update = storage.update_domain_bundle_prep_selection

                def seed_loader(example):
                    assert example == seed["id"]
                    return ExampleBundle.model_validate(copy.deepcopy(seed))

                def aligned_update(*args, **kwargs):
                    result = original_update(*args, **kwargs)
                    if result is None:
                        barrier.wait()
                    return result

                with patch("backend.app.main.load_domain_example", seed_loader), patch.object(
                    storage, "update_domain_bundle_prep_selection", aligned_update
                ):
                    responses = await asyncio.gather(*[
                        client.put(
                            "/api/domain/examples/prep_test_seed/prep-selection",
                            json={"profile_id": profile_id, "card_ids": ids},
                        )
                        for profile_id, ids in [
                            ("cthulhu-dark-2e", [ct_approved]),
                            ("daggerheart", ["card_test_dh_npc"]),
                        ]
                    ])
                assert [response.status_code for response in responses] == [200, 200]
                assert storage.load_domain_bundle(seed["id"])[0]["prep_selections"] == {
                    "cthulhu-dark-2e": [ct_approved],
                    "daggerheart": ["card_test_dh_npc"],
                }
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: preparation selections are validated, isolated, and never touch plans or session state")


if __name__ == "__main__":
    asyncio.run(main())
