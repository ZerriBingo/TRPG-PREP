"""One malformed candidate must not discard the whole window's candidates."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep  # noqa: E402


class FakeTask:
    source_file = "fixture://candidate-normalization"
    source_pages = [1, 2]


def candidate(text: str, kind: str = "clue") -> dict:
    return {
        "text": text,
        "kind": kind,
        "confidence": "high",
        "source_refs": [{"page": 1}],
    }


def main() -> None:
    # The observed failure: a stray object carrying only a confidence field reached strict
    # validation and rejected the whole window, including every good candidate beside it.
    parsed = {
        "candidates": [
            candidate("第一个事实"),
            {"confidence": None},
            candidate("第二个事实"),
            {"text": "   "},
            {"text": "缺少分类", "kind": ""},
            candidate("第三个事实"),
        ]
    }
    normalized = prep._normalize_prep_response(parsed, FakeTask())
    texts = [item["text"] for item in normalized["candidates"]]
    assert texts == ["第一个事实", "第二个事实", "第三个事实"], texts
    assert all(item["kind"] for item in normalized["candidates"])

    # Ordinary candidates keep every field they arrived with.
    kept = prep._normalize_prep_response(
        {"candidates": [candidate("普通事实", "npc")]}, FakeTask()
    )
    assert len(kept["candidates"]) == 1
    assert kept["candidates"][0]["text"] == "普通事实"
    assert kept["candidates"][0]["kind"] == "npc"
    assert "confidence" in kept["candidates"][0]

    # A response that is not a candidate envelope is passed through untouched.
    assert prep._normalize_prep_response({"other": 1}, FakeTask()) == {"other": 1}

    print("PASS: malformed candidates are dropped instead of failing the whole window")


if __name__ == "__main__":
    main()
