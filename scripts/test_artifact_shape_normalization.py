"""Regression check for scalar values returned for list-shaped card fields."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.artifacts import (  # noqa: E402
    ArtifactGenerationError,
    _materialization_messages,
    _validate_global_plan,
    _validate_local_digest,
    _validate_and_build,
    _validate_materialized_card,
)
from backend.domain import (  # noqa: E402
    ExampleBundle,
    RuleProfile,
    SourceFact,
    SourceRef,
    load_profiles,
)


bundle = ExampleBundle(
    id="shape-test",
    name="shape-test",
    description="shape-test",
    profile_ids=["cthulhu-dark-2e"],
    facts=[
        SourceFact(
            id="fact_shape",
            source_refs=[SourceRef(file="fixture://shape", page=1)],
            evidence_status="source_fact",
            text="A door is open.",
            kind="event",
            visibility="explicit",
        ),
        SourceFact(
            id="fact_shape_supporting",
            source_refs=[SourceRef(file="fixture://shape", page=1)],
            evidence_status="source_fact",
            text="The lock can be inspected.",
            kind="clue",
            visibility="explicit",
        ),
    ],
    cards=[],
    plans=[],
)
profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
raw = {
    "cards": [{
        "type": "location",
        "title": "形状测试",
        "subtitle": "测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "可以检查门锁",
            "relevant_characters": "暂无在场人物",
            "direct_clues": ["门锁没有撬痕"],
            "hidden_clues": ["锁可进一步检查"],
            "gm_moves": ["保持调查压力"],
            "return_changes": ["原文未说明变化"],
        },
        "field_sources": {
            "relevant_characters": [["fact_shape"], ["fact_shape_supporting"]],
        },
        "open_questions": ["卡片级问题"],
    }],
    "open_questions": ["响应级问题"],
}
cards, questions = _validate_and_build(raw, bundle, profile, model_id="shape-test")
assert cards[0].fields["relevant_characters"] == ["暂无在场人物"]
assert "fact_shape_supporting" in cards[0].fact_ids
assert set(cards[0].field_sources["relevant_characters"]).issubset(cards[0].fact_ids)
assert questions == ["响应级问题", "卡片级问题"]

nested_provenance = {
    "cards": [{
        "type": "npc",
        "title": "嵌套来源人物",
        "fact_ids": ["fact_shape", "fact_shape_supporting"],
        "fields": {
            "role": {
                "value": "门的看守者",
                "field_sources": ["fact_shape"],
            },
            "wants": "寻找开门的人",
            "offers": "提供门锁的线索",
            "pressure_point": "担心门后内容曝光",
            "refusal_consequence": "拒绝后不再提供线索",
        },
        "field_sources": {"wants": ["fact_shape_supporting"]},
    }],
}
nested_cards, _ = _validate_and_build(
    nested_provenance, bundle, profile, model_id="shape-test", require_runtime_anchor=False
)
assert nested_cards[0].fields["role"] == "门的看守者"
assert nested_cards[0].field_sources["role"] == ["fact_shape"]
assert nested_cards[0].field_sources["wants"] == ["fact_shape_supporting"]

list_item_provenance = {
    "cards": [{
        "type": "location",
        "title": "列表项来源测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "可以检查门锁",
            "relevant_characters": [{
                "text": "门的看守者",
                "field_sources": ["fact_shape_supporting"],
            }],
        },
        "field_sources": {},
    }],
}
list_item_cards, _ = _validate_and_build(
    list_item_provenance, bundle, profile, model_id="shape-test"
)
assert list_item_cards[0].fields["relevant_characters"] == ["门的看守者"]
assert list_item_cards[0].field_sources["relevant_characters"] == ["fact_shape_supporting"]
assert "fact_shape_supporting" in list_item_cards[0].fact_ids

unknown_list_item_key = {
    "cards": [{
        "type": "location",
        "title": "列表项未知键测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "可以检查门锁",
            "relevant_characters": [{
                "text": "门的看守者",
                "field_sources": ["fact_shape"],
                "unsupported": "拒绝",
            }],
        },
    }],
}
try:
    _validate_and_build(unknown_list_item_key, bundle, profile, model_id="shape-test")
except ArtifactGenerationError as error:
    assert "必须是字符串" in str(error)
else:
    raise AssertionError("unknown list item keys must remain rejected")

deep_nested_provenance = {
    "cards": [{
        "type": "location",
        "title": "深层来源测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": {
                "value": {"text": "门开着"},
                "field_sources": ["fact_shape"],
            },
            "arrival_description": "屋外有脚印",
        },
    }],
}
try:
    _validate_and_build(deep_nested_provenance, bundle, profile, model_id="shape-test")
except ArtifactGenerationError as error:
    assert "字段必须是字符串" in str(error)
else:
    raise AssertionError("deeply nested field values must remain rejected")
minimal_location = {
    "cards": [{
        "type": "location",
        "title": "小屋外部",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "屋外有脚印",
        },
        "field_sources": {},
    }],
}
minimal_cards, _ = _validate_and_build(minimal_location, bundle, profile, model_id="shape-test")
assert minimal_cards[0].type == "location"
assert "relevant_characters" not in minimal_cards[0].fields

unknown_source = {
    "cards": [{
        "type": "location",
        "title": "未知来源测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "屋外有脚印",
        },
        "field_sources": {"normal_state": [["fact_missing"]]},
    }],
}
try:
    _validate_and_build(unknown_source, bundle, profile, model_id="shape-test")
except ArtifactGenerationError as error:
    assert "未提升或不存在的事实" in str(error)
else:
    raise AssertionError("unknown field source must remain a hard validation error")

unknown_field = {
    "cards": [{
        "type": "location",
        "title": "未知字段测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "normal_state": "门开着",
            "arrival_description": "屋外有脚印",
            "unsupported_detail": "不能静默保留",
        },
        "field_sources": {},
    }],
}
try:
    _validate_and_build(unknown_field, bundle, profile, model_id="shape-test")
except ArtifactGenerationError as error:
    assert "包含未定义字段" in str(error)
else:
    raise AssertionError("unknown card fields must remain a hard validation error")

planned_card = _validate_materialized_card(
    {
        "cards": [{
            "type": "location",
            "title": "计划闭包测试",
            "subtitle": "测试",
            "fact_ids": ["fact_shape"],
            "fields": {
                "normal_state": "门开着",
                "arrival_description": "可以检查门锁",
            },
            "field_sources": {"normal_state": ["fact_shape"]},
            "open_questions": [],
        }],
        "open_questions": [],
    },
    bundle,
    profile,
    {
        "id": "plan_shape_closure",
        "type": "location",
        "title": "计划闭包测试",
        "fact_ids": ["fact_shape", "fact_shape_supporting"],
    },
    model_id="shape-test",
)
assert planned_card["cards"][0]["fact_ids"] == [
    "fact_shape",
    "fact_shape_supporting",
]

plan_profile = RuleProfile.model_validate({
    "id": "shape-plan-profile",
    "name": "Shape plan profile",
    "version": "1",
    "profile_kind": "runtime",
    "card_definitions": [{
        "type": "location",
        "display_name": "地点",
        "required_fields": [],
        "optional_fields": [],
    }],
})
validated_plan = _validate_global_plan(
    {
        "cards": [{
            "type": "location",
            "title": "计划形状测试",
            "purpose": "验证标量 focus",
            "fact_ids": ["fact_shape"],
            "focus": "调查入口",
            "open_questions": [],
        }],
        "open_questions": [],
    },
    [{"fact_ids": ["fact_shape"]}],
    [bundle.facts[0]],
    plan_profile,
)
assert validated_plan["cards"][0]["focus"] == ["调查入口"]

many_cards = {
    "cards": [
        {
            "type": "location",
            "title": f"独立地点 {index}",
            "purpose": "验证技术安全阈值不决定产品分组",
            "fact_ids": ["fact_shape"],
            "focus": [],
            "open_questions": [],
        }
        for index in range(53)
    ],
    "open_questions": [],
}
validated_many = _validate_global_plan(
    many_cards,
    [{"fact_ids": ["fact_shape"]}],
    [bundle.facts[0]],
    plan_profile,
)
assert len(validated_many["cards"]) == 53

local_units = _validate_local_digest(
    {
        "units": [
            {
                "kind": "clue_cluster",
                "title": f"局部单元 {index}",
                "summary": "保留该局部单元",
                "fact_ids": ["fact_shape"],
                "entity_keys": [],
                "relationship_hints": [],
                "open_questions": [],
            }
            for index in range(33)
        ],
        "open_questions": [],
    },
    [bundle.facts[0]],
    batch_index=1,
)
assert len(local_units["units"]) == 33

location_definition = next(item for item in profile.card_definitions if item.type == "location")
location_plan = {
    "id": "plan_location_prompt",
    "type": "location",
    "title": "灯塔小屋",
    "fact_ids": ["fact_shape"],
}
messages = _materialization_messages(
    profile,
    location_plan,
    [{"id": "fact_shape", "text": "A door is open."}],
)
prompt = "\n".join(message["content"] for message in messages)
for required_field in location_definition.required_fields:
    assert required_field in prompt
assert "MUST be present and non-empty" in prompt
print("PASS: scalar list-shaped card fields normalize deterministically")
