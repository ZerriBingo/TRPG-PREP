"""Regression check for scalar values returned for list-shaped card fields."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.artifacts import _validate_and_build  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402


bundle = ExampleBundle(
    id="shape-test",
    name="shape-test",
    description="shape-test",
    profile_ids=["cthulhu-dark-2e"],
    facts=[SourceFact(
        id="fact_shape",
        source_refs=[SourceRef(file="fixture://shape", page=1)],
        evidence_status="source_fact",
        text="A door is open.",
        kind="event",
        visibility="explicit",
    )],
    cards=[],
    plans=[],
)
profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
raw = {
    "cards": [{
        "type": "scene",
        "title": "形状测试",
        "subtitle": "测试",
        "fact_ids": ["fact_shape"],
        "fields": {
            "opening_image": "门开着",
            "immediate_actions": "先检查门锁",
            "direct_clues": ["门锁没有撬痕"],
            "hidden_clues": ["暂无"],
            "gm_moves": ["保持压力"],
            "risk_if_pressed": ["暴露"],
            "exit_conditions": ["离开"],
        },
        "field_sources": {},
        "open_questions": [],
    }],
    "open_questions": [],
}
cards, _ = _validate_and_build(raw, bundle, profile, model_id="shape-test")
assert cards[0].fields["immediate_actions"] == ["先检查门锁"]
print("PASS: scalar list-shaped card fields normalize deterministically")
