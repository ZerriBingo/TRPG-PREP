"""A cross-chapter merge is refused only when the merged closure breaks the budget."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a  # noqa: E402
from backend.domain import SourceFact, SourceRef  # noqa: E402


def _facts(count: int, size: int) -> dict:
    built = [
        SourceFact(
            id=f"fact_merge_{index:04d}",
            text="x" * size,
            kind="event",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://merge-budget", page=1)],
        )
        for index in range(count)
    ]
    return {fact.id: fact for fact in built}


def main():
    facts_by_id = _facts(4, 200)
    left = {
        "type": "npc",
        "title": "同名",
        "fact_ids": ["fact_merge_0000", "fact_merge_0001"],
    }
    right = {
        "type": "npc",
        "title": "同名",
        "fact_ids": ["fact_merge_0002", "fact_merge_0003"],
    }

    # A closure inside the materialize budget still merges.
    assert len(a._merge_chapter_plans([], [left, right], facts_by_id)) == 1

    # The same pair stops merging once the closure exceeds the character budget,
    # even though four facts sit far below the structural ceiling.
    with patch.object(a, "MAX_CARD_FACT_INPUT_CHARS", 400):
        assert len(a._merge_chapter_plans([], [left, right], facts_by_id)) == 2

    # The token budget is enforced the same way.
    with patch.object(a, "MAX_CARD_FACT_INPUT_TOKENS", 1):
        assert len(a._merge_chapter_plans([], [left, right], facts_by_id)) == 2

    # The structural ceiling is loose enough that it cannot decide a merge first.
    assert a.MAX_MERGED_FACT_IDS > 4

    # A caller holding no fact table keeps the older fact-count ceiling.
    assert len(a._merge_chapter_plans([], [left, right])) == 1
    with patch.object(a, "MAX_MERGED_FACT_IDS", 3):
        assert len(a._merge_chapter_plans([], [left, right])) == 2

    print("PASS: merges are bounded by the materialize budget, not by a fact count")


if __name__ == "__main__":
    main()
