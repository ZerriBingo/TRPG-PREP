"""Public domain-contract checks for environment-led fantasy preparation."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import (  # noqa: E402
    DerivedCard,
    DomainValidationError,
    ExampleBundle,
    SourceFact,
    SourceRef,
    draft_scene_plan,
    load_profiles,
    validate_bundle,
)


def fact(fact_id: str, text: str, kind: str, page: int) -> SourceFact:
    return SourceFact(
        id=fact_id,
        text=text,
        kind=kind,
        visibility="explicit",
        source_refs=[SourceRef(file="fixture://fantasy-adventure", page=page)],
    )


def card(
    card_id: str,
    card_type: str,
    title: str,
    fact_ids: list[str],
    fields: dict,
    runtime_group_ids: list[str] | None = None,
) -> DerivedCard:
    return DerivedCard(
        id=card_id,
        profile_id="daggerheart",
        type=card_type,
        title=title,
        fact_ids=fact_ids,
        fields=fields,
        field_sources={key: list(fact_ids) for key in fields},
        runtime_group_ids=runtime_group_ids or [],
        edit_state="approved",
    )


def main() -> None:
    profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
    fantasy = profiles["daggerheart"]
    assert [item.type for item in fantasy.card_definitions] == [
        "environment",
        "npc",
        "enemy",
        "encounter_clock",
    ]

    facts = [
        fact("fact_premise", "一只会说话的羊请求帮助。", "event", 1),
        fact("fact_grove", "法师在林中庄园准备仪式。", "location", 2),
        fact("fact_wizard", "法师想完成仪式并守住庄园。", "threat", 2),
        fact("fact_timer", "仪式完成后受害者将无法恢复。", "timeline", 2),
    ]
    cards = [
        card(
            "card_grove",
            "environment",
            "法师庄园",
            ["fact_grove", "fact_wizard"],
            {
                "environment_type": "event",
                "opening_image": "仪式光芒穿过林地。",
                "narrative_function": "迫使众人在救援与阻止仪式之间选择。",
                "impulses": ["保护仪式", "隔开闯入者"],
                "usable_features": ["林木与围墙可供掩护"],
                "hazards": ["失控的变形魔法"],
                "gm_responses": ["先展示仪式进一步失控"],
                "phase_changes": ["仪式被打断后庄园开始坍塌"],
                "exit_outcomes": ["救出受害者", "法师逃走"],
            },
            ["encounter_grove"],
        ),
        card(
            "card_wizard",
            "enemy",
            "庄园法师",
            ["fact_wizard"],
            {
                "motives": ["完成仪式"],
                "current_objective": "拖延闯入者。",
                "signature_actions": ["改变地形"],
                "danger_signs": ["魔力向仪式中心汇聚"],
                "damaged_behavior": "放弃正面对抗并靠近仪式。",
                "resolutions": ["谈判", "迫使其撤退", "击败"],
            },
            ["encounter_grove"],
        ),
        card(
            "card_ritual",
            "encounter_clock",
            "变形仪式",
            ["fact_timer"],
            {
                "name": "变形仪式",
                "stages": ["魔力汇聚", "形体固化", "仪式完成"],
                "advance_condition": "众人拖延或以恐惧结果行动。",
            },
            ["encounter_grove"],
        ),
    ]
    bundle = ExampleBundle(
        id="fantasy_contract",
        name="奇幻冒险契约",
        profile_ids=[fantasy.id],
        facts=facts,
        cards=cards,
        plans=[],
    )
    validate_bundle(bundle, profiles)

    plan = draft_scene_plan(
        bundle,
        fantasy.id,
        [item.id for item in cards],
        "变形法术风波",
        "fixture://fantasy-adventure",
        [1, 2],
        "阻止仪式并决定如何帮助受害者。",
        profile=fantasy,
    )
    assert plan.navigation_mode == "environment"
    assert plan.environment_card_ids == ["card_grove"]
    assert plan.location_card_ids == []
    assert plan.beats == []

    broken = bundle.model_copy(deep=True)
    broken.cards[2].runtime_group_ids = ["encounter_missing"]
    try:
        validate_bundle(broken, profiles)
    except DomainValidationError as error:
        assert "missing environment anchor" in str(error)
    else:
        raise AssertionError("support cards must not point to an unknown encounter group")

    ungrouped = bundle.model_copy(deep=True)
    ungrouped.cards[0].runtime_group_ids = []
    ungrouped.cards[1].runtime_group_ids = []
    ungrouped.cards[2].runtime_group_ids = []
    validate_bundle(ungrouped, profiles)

    legacy = bundle.model_copy(deep=True)
    legacy.cards[0].type = "adventure_overview"
    try:
        validate_bundle(legacy, profiles)
    except DomainValidationError as error:
        assert "undefined type" in str(error)
    else:
        raise AssertionError("retired card types must fail through the domain error boundary")

    print("PASS: fantasy preparation is grouped around runnable environments")


if __name__ == "__main__":
    main()
