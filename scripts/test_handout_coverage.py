"""Regression checks for the independent display-material boundary."""
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
from backend.domain import (  # noqa: E402
    DerivedCard,
    DisplayMaterial,
    ExampleBundle,
    SourceFact,
    SourceRef,
    draft_scene_plan,
    load_profiles,
    validate_bundle,
)

# Auto-labelled display materials must remain valid SourceFact records: the
# material classification belongs in ``kind``, while visibility stays in the
# shared evidence vocabulary.
PREP_SOURCE = (ROOT / "backend" / "app" / "prep.py").read_text(encoding="utf-8")
assert 'kind="handout"' in PREP_SOURCE
assert 'visibility="explicit"' in PREP_SOURCE
assert 'visibility="handout"' not in PREP_SOURCE
assert "_ensure_labeled_display_materials(bundle, job)" not in PREP_SOURCE


def material_bundle() -> ExampleBundle:
    return ExampleBundle(
        id="display_material_contract_test",
        name="Display material contract test",
        profile_ids=["cthulhu-dark-2e"],
        facts=[
            SourceFact(
                id="fact_map_source",
                source_refs=[SourceRef(file="fixture://lighthouse", page=13)],
                evidence_status="source_fact",
                text="航标岛灯塔地图",
                kind="handout",
                visibility="explicit",
            ),
            SourceFact(
                id="fact_lighthouse_location",
                source_refs=[SourceRef(file="fixture://lighthouse", page=14)],
                evidence_status="source_fact",
                text="灯塔内部可以调查。",
                kind="location",
                visibility="explicit",
            ),
        ],
        cards=[],
        plans=[],
    )


def location_card() -> DerivedCard:
    return DerivedCard(
        id="card_location_contract",
        profile_id="cthulhu-dark-2e",
        type="location",
        title="灯塔现场",
        fact_ids=["fact_lighthouse_location"],
        fields={
            "normal_state": "潮湿的灯塔内部",
            "arrival_description": "潮气与海风穿过入口。",
            "relevant_characters": ["守塔人"],
            "direct_clues": ["脚印"],
            "hidden_clues": ["暗门"],
            "gm_moves": ["推进压力"],
            "return_changes": ["风暴继续增强"],
        },
        field_sources={"normal_state": ["fact_lighthouse_location"]},
        edit_state="approved",
    )


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "display-material.db"
        try:
            storage.init_db()
            bundle = material_bundle()
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json"))
            profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")

            # A handout fact is not a location and is excluded from artifact input.
            assert [fact.id for fact in artifacts._facts_for_workspace(bundle, bundle.id)] == [
                "fact_lighthouse_location"
            ]

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                workbench = await client.get(
                    "/api/domain/workbench", params={"example": bundle.id}
                )
                assert workbench.status_code == 200, workbench.text
                payload = workbench.json()
                assert "source_checks" not in payload
                assert payload["handout_fact_ids"] == ["fact_map_source"]

                created = await client.post(
                    f"/api/domain/examples/{bundle.id}/display-materials",
                    json={"source_fact_id": "fact_map_source"},
                )
                assert created.status_code == 200, created.text
                material = created.json()["material"]
                assert "player_content" not in material

            bundle.display_materials = [DisplayMaterial.model_validate(material)]
            bundle.cards = [location_card()]
            plan = draft_scene_plan(
                bundle,
                "cthulhu-dark-2e",
                ["card_location_contract"],
                "灯塔测试",
                "fixture://lighthouse",
                [14],
                "调查灯塔",
                profiles["cthulhu-dark-2e"],
            )
            assert plan.navigation_mode == "location"
            assert plan.beats == []
            bundle.plans = [plan]
            validate_bundle(bundle, profiles)
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json"))

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                selected = [{"plan_id": plan.id, "card_id": "card_location_contract"}]
                updated = await client.put(
                    f"/api/domain/examples/{bundle.id}/display-materials/{material['id']}",
                    json={"title": "航标岛灯塔地图", "gm_notes": "需要辨认岛上位置时展示。", "links": selected},
                )
                assert updated.status_code == 200, updated.text

                locked = ExampleBundle.model_validate(storage.load_domain_bundle(bundle.id)[0])
                locked.plans[0].title = "不应直接改写"
                rejected = await client.put(
                    f"/api/domain/examples/{bundle.id}/bundle",
                    json=locked.model_dump(mode="json"),
                )
                assert rejected.status_code == 409, rejected.text

            saved = ExampleBundle.model_validate(storage.load_domain_bundle(bundle.id)[0])
            validate_bundle(saved, profiles)
            assert saved.display_materials[0].links[0].card_id == "card_location_contract"
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: display materials stay independent and location-specific")


if __name__ == "__main__":
    asyncio.run(main())
