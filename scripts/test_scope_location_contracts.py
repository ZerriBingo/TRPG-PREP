"""Regression checks for task-owned runtime scope and location coverage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts  # noqa: E402
from backend.app.llm import FakeLLM  # noqa: E402
from backend.domain import (  # noqa: E402
    DerivedCard,
    ExampleBundle,
    RuleProfile,
    SourceFact,
    SourceRef,
)


def main() -> None:
    profile = RuleProfile.model_validate(
        {
            "id": "fixture-runtime",
            "name": "Fixture runtime",
            "version": "1",
            "profile_kind": "runtime",
            "card_definitions": [
                {
                    "type": "location",
                    "display_name": "地点",
                    "required_fields": ["normal_state"],
                    "optional_fields": [],
                }
            ],
        }
    )
    facts = [
        SourceFact(
            id="fact_in_scope",
            text="当前章节地点",
            kind="location",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://chapter", page=2)],
        ),
        SourceFact(
            id="fact_other_chapter",
            text="另一章地点",
            kind="location",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://chapter", page=20)],
        ),
        SourceFact(
            id="fact_other_file",
            text="另一文件地点",
            kind="location",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://other", page=2)],
        ),
    ]
    bundle = ExampleBundle(
        id="scope_contract",
        name="Scope contract",
        profile_ids=[profile.id],
        facts=facts,
        cards=[
            DerivedCard(
                id="card_in_scope",
                profile_id=profile.id,
                type="location",
                title="当前章节地点",
                fact_ids=["fact_in_scope"],
                fields={"normal_state": "入口"},
                edit_state="approved",
            ),
            DerivedCard(
                id="card_other_chapter",
                profile_id=profile.id,
                type="location",
                title="另一章地点",
                fact_ids=["fact_other_chapter"],
                fields={"normal_state": "入口"},
                edit_state="approved",
            ),
            DerivedCard(
                id="card_other_file",
                profile_id=profile.id,
                type="location",
                title="另一文件地点",
                fact_ids=["fact_other_file"],
                fields={"normal_state": "入口"},
                edit_state="approved",
            ),
        ],
        plans=[],
    )

    plan = __import__("backend.domain", fromlist=["draft_scene_plan_from_workspace"]).draft_scene_plan_from_workspace(
        bundle,
        {profile.id: profile},
        profile_id=profile.id,
        source_file="fixture://chapter",
        source_pages=[2, 3],
    )
    assert plan.card_ids == ["card_in_scope"], plan.card_ids

    units = [
        {
            "id": "unit_police",
            "kind": "location",
            "title": "警察局",
            "summary": "可调查并可返回的地点",
            "fact_ids": ["fact_in_scope"],
            "entity_keys": ["警察局"],
            "relationship_hints": [],
            "open_questions": [],
            "source_refs": [{"file": "fixture://chapter", "page": 2}],
        },
        {
            "id": "unit_publisher",
            "kind": "location",
            "title": "出版社",
            "summary": "可调查并可返回的地点",
            "fact_ids": ["fact_in_scope"],
            "entity_keys": ["出版社"],
            "relationship_hints": [],
            "open_questions": [],
            "source_refs": [{"file": "fixture://chapter", "page": 2}],
        },
    ]
    raw_plan = {
        "cards": [
            {
                "type": "location",
                "title": "警察局",
                "purpose": "独立调查地点",
                "fact_ids": ["fact_in_scope"],
                "focus": ["警察局"],
                "open_questions": [],
            }
        ],
        "open_questions": [],
    }
    validated = artifacts._validate_global_plan(raw_plan, units, [facts[0]], profile)
    assert [card["title"] for card in validated["cards"]] == ["警察局"]

    messages = artifacts._global_plan_messages(
        profile,
        units,
        {"fact_in_scope": 20},
        {"fact_in_scope": 10},
    )
    user_message = messages[1]["content"]
    assert "ALLOWED_CARD_DEFINITIONS_JSON" not in user_message
    compact_units = json.loads(
        next(
            line.removeprefix("GLOBAL_UNITS_JSON=")
            for line in user_message.splitlines()
            if line.startswith("GLOBAL_UNITS_JSON=")
        )
    )
    assert all("source_refs" not in unit for unit in compact_units)

    fake_plan = FakeLLM({"model": "fake-location-contract"}).chat_json(messages)
    fake_locations = [card for card in fake_plan["cards"] if card["type"] == "location"]
    assert len(fake_locations) == 2, fake_locations

    print("PASS: runtime plans stay task-scoped without mechanical location rejection")


if __name__ == "__main__":
    main()
