"""One environment card owns one situation, and case_scope is explicit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a  # noqa: E402
from backend.domain import SourceFact, SourceRef, load_profiles  # noqa: E402

FACT_ID = "fact_situation_boundary"
FACT = SourceFact(
    id=FACT_ID,
    text="The manor ritual and the flooded vault are separate situations.",
    kind="event",
    visibility="explicit",
    source_refs=[SourceRef(file="fixture://situation-boundary", page=1)],
)
FACTS = [FACT]
UNITS = [{"fact_ids": [FACT_ID]}]
PROFILE = load_profiles(ROOT / "backend" / "domain" / "profiles")["daggerheart"]


def _plan(card: dict) -> dict:
    return a._validate_global_plan(
        {
            "cards": [card],
            "brief": {
                "premise": "A manor with two independent pressures.",
                "current_situation": "The table starts at the ritual.",
                "fact_ids": [FACT_ID],
            },
        },
        UNITS,
        FACTS,
        PROFILE,
    )


def _environment(**extra) -> dict:
    card = {
        "type": "environment",
        "title": "庄园仪式",
        "purpose": "提供可运行环境",
        "fact_ids": [FACT_ID],
    }
    card.update(extra)
    return card


def main():
    # Two declared groups mean the model bundled independent situations.
    try:
        _plan(_environment(runtime_groups=["庄园仪式", "水淹地窖"]))
    except a.ArtifactGenerationError as error:
        assert "独立情境" in str(error), str(error)
        assert "拆分" in str(error), str(error)
    else:
        raise AssertionError("an environment card must not bundle two situations")

    # A single declared group names the situation when the model omits it.
    single = _plan(_environment(runtime_groups=["庄园仪式"]))
    card = single["cards"][0]
    assert card["situation"] == "庄园仪式", card["situation"]
    assert card["case_scope"] == "current", card["case_scope"]
    assert len(card["runtime_group_ids"]) == 1, card["runtime_group_ids"]

    # No group and no situation: fall back to the title and ask for review.
    bare = _plan(_environment())
    bare_card = bare["cards"][0]
    assert bare_card["situation"] == "庄园仪式", bare_card["situation"]
    assert any("未声明可运行情境" in item for item in bare["open_questions"]), bare["open_questions"]

    # An explicit situation wins, trimmed, and case_scope is preserved.
    explicit = _plan(
        _environment(
            situation="  水淹地窖  ",
            runtime_groups=["水淹地窖"],
            case_scope="conditional_example",
        )
    )
    explicit_card = explicit["cards"][0]
    assert explicit_card["situation"] == "水淹地窖", explicit_card["situation"]
    assert explicit_card["case_scope"] == "conditional_example", explicit_card["case_scope"]

    # An unknown case_scope is a schema error, not a silent default.
    try:
        _plan(_environment(case_scope="someday"))
    except a.ArtifactGenerationError as error:
        assert "不符合约定" in str(error), str(error)
    else:
        raise AssertionError("an unknown case_scope must be rejected")

    # Both keys are planning-only: the materialization prompt must not see them.
    prompt_plan = a._prompt_plan(explicit_card)
    assert "situation" not in prompt_plan, prompt_plan
    assert "case_scope" not in prompt_plan, prompt_plan
    assert prompt_plan["fact_ids"] == [FACT_ID], prompt_plan

    print("PASS: one situation per environment card, explicit case_scope, planning-only keys")


if __name__ == "__main__":
    main()
