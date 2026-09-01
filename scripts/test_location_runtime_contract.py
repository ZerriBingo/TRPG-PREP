"""Contract checks for location-led reality-horror runtime plans."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import (  # noqa: E402
    DerivedCard,
    DisplayMaterial,
    DisplayMaterialLink,
    ExampleBundle,
    SessionState,
    SourceFact,
    SourceRef,
    draft_scene_plan,
    load_profiles,
    validate_session,
    validate_bundle,
)


def main() -> None:
    profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
    profile = profiles["cthulhu-dark-2e"]
    facts = [
        SourceFact(
            id="fact_lighthouse",
            text="灯塔可调查并可折返。",
            kind="location",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://module", page=13)],
        ),
        SourceFact(
            id="fact_station",
            text="警察局可以提供帮助。",
            kind="location",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://module", page=14)],
        ),
        SourceFact(
            id="fact_map",
            text="灯塔地图。",
            kind="handout",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://module", page=13)],
        ),
    ]
    cards = [
        DerivedCard(
            id="card_lighthouse",
            profile_id=profile.id,
            type="location",
            title="灯塔",
            fact_ids=["fact_lighthouse"],
            fields={
                "normal_state": "灯火熄灭。",
                "arrival_description": "海风穿过破窗。",
                "relevant_characters": ["守塔人"],
                "direct_clues": ["门锁被撬开。"],
                "hidden_clues": ["地下室留有脚印。"],
                "gm_moves": ["让灯塔结构发出异响。"],
                "return_changes": ["风暴加剧。"],
                "first_triggers": ["首次进入塔顶时发现破损灯具。"],
            },
            edit_state="approved",
        ),
        DerivedCard(
            id="card_station",
            profile_id=profile.id,
            type="location",
            title="警察局",
            fact_ids=["fact_station"],
            fields={
                "normal_state": "夜班警员留守。",
                "arrival_description": "大厅灯光惨白。",
                "relevant_characters": ["值班警员"],
                "direct_clues": ["值班记录可供查询。"],
                "hidden_clues": ["旧档案里有一次未结案记录。"],
                "gm_moves": ["要求说明来意。"],
                "return_changes": ["换班后态度改变。"],
            },
            edit_state="approved",
        ),
    ]
    bundle = ExampleBundle(
        id="location_runtime",
        name="地点运行契约",
        profile_ids=[profile.id],
        facts=facts,
        cards=cards,
        plans=[],
    )
    plan = draft_scene_plan(
        bundle,
        profile.id,
        [card.id for card in cards],
        "地点运行",
        "fixture://module",
        [13, 14],
        "从任意地点开始。",
        profile=profile,
    )

    assert plan.navigation_mode == "location"
    assert plan.beats == []
    assert plan.location_card_ids == ["card_lighthouse", "card_station"]

    bundle.plans = [plan]
    bundle.display_materials = [
        DisplayMaterial(
            id="material_lighthouse_map",
            title="灯塔地图",
            source_fact_ids=["fact_map"],
            source_refs=[SourceRef(file="fixture://module", page=13)],
            links=[DisplayMaterialLink(plan_id=plan.id, card_id="card_lighthouse")],
        )
    ]
    validate_bundle(bundle, profiles)
    session = SessionState(
        example_id=bundle.id,
        current_plan_id=plan.id,
        current_card_id="card_lighthouse",
        trigger_states={"card_lighthouse:first:0": "active"},
    )
    validate_session(session, bundle)
    assert session.current_beat_id is None
    assert session.trigger_states["card_lighthouse:first:0"] == "active"
    print("PASS: reality-horror runtime is location-led and tracks trigger state")


if __name__ == "__main__":
    main()
