"""Over-budget index summaries must shrink to a decision, never spin forever."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a  # noqa: E402


def main():
    items = [{"summary": "abcd"}, {"summary": "x"}, {"summary": ""}, {}]

    assert a._shrink_summaries(items) is True
    assert items[0]["summary"] == "abc", items[0]
    # A length-1 summary must shrink to empty; a floor would stall the loop.
    assert items[1]["summary"] == "", items[1]
    assert items[2]["summary"] == "", items[2]

    assert a._shrink_summaries(items) is True
    assert items[0]["summary"] == "ab", items[0]
    assert a._shrink_summaries(items) is True
    assert items[0]["summary"] == "a", items[0]
    assert a._shrink_summaries(items) is True
    assert items[0]["summary"] == "", items[0]

    # Nothing is left to shrink, so the caller raises instead of looping.
    assert a._shrink_summaries(items) is False
    assert a._shrink_summaries([]) is False

    print("PASS: index summaries shrink to empty so an over-budget request terminates")


if __name__ == "__main__":
    main()
