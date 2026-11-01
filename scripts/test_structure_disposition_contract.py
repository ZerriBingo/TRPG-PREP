"""Module design notes and preparation guidance must not become a chapter."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import structure  # noqa: E402


def main() -> None:
    facts = [
        {
            "id": "fact_design_goal",
            "text": "The revision lists three goals for itself.",
            "kind": "event",
            "visibility": "explicit",
            "source_refs": [{"file": "fixture://structure", "page": 1}],
        }
    ]
    skeleton = {
        "chapters": [{"key": "ch1", "title": "Opening"}],
        "threads": [{"key": "t1", "title": "Thread"}],
    }
    messages = structure._assignment_messages(facts, skeleton, 0, 1)
    system = messages[0]["content"]

    # A chapter is material the PCs can encounter; commentary about the module is
    # reference material.  The previous wording only permitted global_reference,
    # which let a full chapter be built out of design notes.
    assert "are not a playable chapter" in system
    assert "never create a chapter key for preparation guidance alone" in system
    assert "Every chapter must contain material the PCs can encounter in play" in system
    assert "general rules commentary, and non-actionable context may remain global_reference" in system
    assert structure.STRUCTURE_PROMPT_VERSION == "adventure-structure-v7"

    print("PASS: module design and preparation guidance cannot become a chapter")


if __name__ == "__main__":
    main()
