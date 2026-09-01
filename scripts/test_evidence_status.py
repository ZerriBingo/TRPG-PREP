"""Regression checks for auditable fact evidence and runtime candidate isolation."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "domain"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import (  # noqa: E402
    DomainValidationError,
    ExampleBundle,
    SourceFact,
    draft_scene_plan,
    load_json,
    load_profiles,
    validate_bundle,
)


def expect_rejection(label: str, action) -> None:
    try:
        action()
    except DomainValidationError as error:
        print(f"PASS: {label}: {error}")
        return
    raise AssertionError(f"Expected rejection: {label}")


def main() -> None:
    profiles = load_profiles(DOMAIN / "profiles")
    bundle = ExampleBundle.model_validate(
        load_json(DOMAIN / "examples" / "evidence_status_fixture.json")
    )
    validate_bundle(bundle, profiles)

    facts = {fact.id: fact for fact in bundle.facts}
    source_fact = facts["fact_evidence_source"]
    candidate_fact = facts["fact_evidence_candidate"]
    gm_fact = facts["fact_evidence_gm"]
    assert source_fact.evidence_status == "source_fact"
    assert len(source_fact.source_refs) == 2
    assert source_fact.source == source_fact.source_refs[0]
    assert candidate_fact.evidence_status == "model_candidate"
    assert candidate_fact.source is None and not candidate_fact.source_refs
    assert gm_fact.evidence_status == "gm_authored"
    print("PASS: source, candidate, and GM-authored facts remain distinct")

    legacy = SourceFact.model_validate({
        "id": "fact_legacy_source",
        "source": {"file": "fixture://legacy", "page": 1},
        "text": "Legacy source field remains readable.",
        "kind": "clue",
        "visibility": "explicit",
    })
    assert legacy.evidence_status == "source_fact"
    assert len(legacy.source_refs) == 1
    print("PASS: legacy source field normalizes to source_refs")

    approved_candidate = bundle.model_copy(deep=True)
    approved_candidate.cards[0].fact_ids.append("fact_evidence_candidate")
    expect_rejection(
        "approved card cannot cite a model candidate",
        lambda: validate_bundle(approved_candidate, profiles),
    )

    planned_candidate = bundle.model_copy(deep=True)
    planned_candidate.cards[1].edit_state = "approved"
    planned_candidate.plans[0].card_ids.append("card_evidence_candidate_draft")
    planned_candidate.plans[0].location_card_ids.append("card_evidence_candidate_draft")
    expect_rejection(
        "runtime plan cannot include a candidate-backed card",
        lambda: validate_bundle(planned_candidate, profiles),
    )

    expect_rejection(
        "draft builder cannot select a candidate-backed card",
        lambda: draft_scene_plan(
            bundle,
            "cthulhu-dark-2e",
            ["card_evidence_candidate_draft"],
            "Candidate draft",
            "fixture://evidence-status",
            [1],
            "This must stay outside runtime.",
            profile=profiles["cthulhu-dark-2e"],
        ),
    )

    print("Evidence status regression checks passed")


if __name__ == "__main__":
    main()
