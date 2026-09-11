"""Prompt and provenance contracts for environment-led fantasy preparation."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef, load_profiles  # noqa: E402


profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
profile = profiles["daggerheart"]
facts = [
    SourceFact(
        id="fact_fantasy_prompt",
        text="林中庄园的仪式迫使冒险者在救援与追击之间选择。",
        kind="event",
        visibility="explicit",
        source_refs=[SourceRef(file="fixture://fantasy-prompt", page=1)],
    )
]
fact_payload = [fact.model_dump(mode="json") for fact in facts]

local_system = artifacts._local_digest_messages(
    profile, fact_payload, batch_index=1, batch_count=1
)[0]["content"]
assert "traversal" in local_system
assert "faction" in local_system
assert "playable situation" in local_system
assert "not every named place" in local_system

global_system = artifacts._global_plan_messages(
    profile,
    [
        {
            "id": "local_unit_1",
            "kind": "scene",
            "title": "庄园仪式",
            "summary": facts[0].text,
            "fact_ids": [facts[0].id],
            "entity_keys": ["林中庄园"],
            "relationship_hints": [],
            "open_questions": [],
        }
    ],
    {facts[0].id: len(facts[0].text)},
    {facts[0].id: 20},
)[0]["content"]
assert "playable situation" in global_system
assert "not every named place" in global_system
assert "exploration, social, traversal, combat, or event" in global_system
assert "do not generate or convert game statistics" in global_system.lower()

bundle = ExampleBundle(
    id="fantasy_prompt_contract",
    name="奇幻提示词契约",
    profile_ids=[profile.id],
    facts=facts,
    cards=[],
    plans=[],
)
raw = {
    "cards": [
        {
            "type": "environment",
            "title": "庄园仪式",
            "fact_ids": [facts[0].id],
            "fields": {
                "environment_type": "event",
                "opening_image": "仪式光芒穿过林地。",
                "narrative_function": "迫使冒险者在救援与追击之间选择。",
                "impulses": ["保护仪式"],
                "usable_features": ["林地掩护"],
                "hazards": ["失控魔法"],
                "gm_responses": ["让仪式显露新的代价"],
                "phase_changes": ["仪式被打断"],
                "exit_outcomes": ["受害者获救"],
            },
            "field_sources": {
                "environment_type": [facts[0].id],
                "opening_image": [facts[0].id],
                "narrative_function": [facts[0].id],
                "impulses": [facts[0].id],
                "usable_features": [facts[0].id],
                "hazards": [facts[0].id],
                "gm_responses": [],
                "phase_changes": [facts[0].id],
                "exit_outcomes": [facts[0].id],
            },
        }
    ],
    "open_questions": [],
}
cards, _ = artifacts._validate_and_build(raw, bundle, profile, model_id="fixture-fantasy")
assert cards[0].field_sources["gm_responses"] == []

print("PASS: fantasy prompts preserve situation-led planning and GM-authored provenance")
