"""State anchors stay out of open_questions, and cards carry the two new fields."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a  # noqa: E402
from backend.domain import load_profiles  # noqa: E402

FACT = {
    "id": "fact_state_anchor",
    "text": "The manor is closed until the party reopens it.",
    "kind": "event",
    "visibility": "explicit",
    "evidence_status": None,
    "source_refs": [{"file": "fixture://state-anchor", "page": 1}],
    "tags": [],
    "notes": None,
}


def main():
    guidance = a.SOURCE_SEMANTIC_GUIDANCE
    # Which optional events occurred and where a table stands in time depend on
    # player action, so they must never be routed into review questions.
    assert "state what the material assumes in assumed_state" in guidance
    assert "never turn them into review questions" in guidance
    assert "record the uncertainty in open_questions" not in guidance, guidance

    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["daggerheart"]
    assert profile.card_definitions, "profile has no card definitions"
    for definition in profile.card_definitions:
        # Optional on purpose: a required field with no source support is what
        # pushes the model to invent a value.
        assert "assumed_state" in definition.optional_fields, definition.type
        assert "table_questions" in definition.optional_fields, definition.type
        assert "assumed_state" not in definition.required_fields, definition.type
        assert "table_questions" not in definition.required_fields, definition.type

    # One is a list, the other a single declarative sentence.
    assert "table_questions" in a.LIST_FIELDS
    assert "assumed_state" not in a.LIST_FIELDS

    for definition in profile.card_definitions:
        plan = {"type": definition.type, "title": "State anchor probe", "fact_ids": [FACT["id"]]}
        messages = a._materialization_messages(profile, plan, [FACT])
        user = messages[1]["content"]
        payload = {
            line.split("=", 1)[0]: json.loads(line.split("=", 1)[1])
            for line in user.splitlines()
            if "_JSON=" in line
        }
        assert "table_questions" in payload["LIST_FIELDS_JSON"], definition.type
        assert "assumed_state" in payload["CARD_DEFINITION_JSON"]["optional_fields"]
        system = messages[0]["content"]
        assert "assumed_state is one concise declarative sentence" in system
        assert "table_questions holds two to four short prompts" in system

    print("PASS: state anchors stay out of review, and cards carry an assumed state and table prompts")


if __name__ == "__main__":
    main()
