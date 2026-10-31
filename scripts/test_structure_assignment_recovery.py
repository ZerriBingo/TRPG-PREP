"""An unclosed assignment batch is repaired, then deferred, never fatal."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import structure  # noqa: E402
from backend.domain import SourceFact, SourceRef  # noqa: E402

SKELETON = {"chapters": [{"key": "ch1"}], "threads": []}


def fact(fact_id: str) -> SourceFact:
    return SourceFact(
        id=fact_id,
        text=f"fixture fact {fact_id}",
        kind="event",
        visibility="explicit",
        source_refs=[SourceRef(file="fixture://assignment-recovery", page=1)],
    )


FACTS = [fact("fact_a"), fact("fact_b"), fact("fact_c")]


def assigned(fact_id: str) -> dict:
    return {
        "fact_id": fact_id,
        "kind": "chapter",
        "chapter_keys": ["ch1"],
        "thread_keys": [],
        "reason": "",
    }


def raise_closure() -> structure.StructureAssignmentClosureError:
    raw = {"assignments": [assigned("fact_a"), assigned("fact_b")]}
    try:
        structure._validate_assignments(raw, FACTS, SKELETON)
    except structure.StructureAssignmentClosureError as error:
        return error
    raise AssertionError("an unclosed batch must raise")


def main() -> None:
    # The error must carry what did arrive, so the caller does not lose the batch.
    error = raise_closure()
    assert error.missing == ["fact_c"], error.missing
    assert [item["fact_id"] for item in error.partial] == ["fact_a", "fact_b"], error.partial

    # If the repair request also fails, the unassigned fact is deferred with a reason
    # rather than failing the build, and the batch order is preserved.
    with patch.object(structure, "run_json_step", side_effect=RuntimeError("offline")):
        recovered = structure._recover_assignments(None, None, SKELETON, FACTS, 1, 1, error)
    assert [item["fact_id"] for item in recovered] == ["fact_a", "fact_b", "fact_c"], recovered
    assert recovered[2]["kind"] == "deferred"
    assert recovered[2]["reason"].strip(), recovered[2]
    assert recovered[2]["chapter_keys"] == []

    # When the repair request succeeds, the missing fact keeps its real assignment.
    with patch.object(
        structure, "run_json_step", return_value={"assignments": [assigned("fact_c")]}
    ):
        repaired = structure._recover_assignments(None, None, SKELETON, FACTS, 1, 1, error)
    assert [item["fact_id"] for item in repaired] == ["fact_a", "fact_b", "fact_c"], repaired
    assert all(item["kind"] == "chapter" for item in repaired), repaired

    # A complete batch still closes cleanly.
    complete = {"assignments": [assigned("fact_a"), assigned("fact_b"), assigned("fact_c")]}
    assert len(structure._validate_assignments(complete, FACTS, SKELETON)["assignments"]) == 3

    print("PASS: an unclosed assignment batch is repaired, then deferred, never fatal")


if __name__ == "__main__":
    main()
