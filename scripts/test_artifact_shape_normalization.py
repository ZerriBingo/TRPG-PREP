"""Regression check for scalar values returned for list-shaped card fields."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.artifacts import _materialization_messages, _validate_and_build  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402


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
            "relevant_characters": ["fact_shape", "fact_shape_supporting"],
        },
        "open_questions": [],
    }],
    "open_questions": [],
}
cards, _ = _validate_and_build(raw, bundle, profile, model_id="shape-test")
assert cards[0].fields["relevant_characters"] == ["暂无在场人物"]
assert "fact_shape_supporting" in cards[0].fact_ids
assert set(cards[0].field_sources["relevant_characters"]).issubset(cards[0].fact_ids)
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
