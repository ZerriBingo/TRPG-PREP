"""Check the actual question tables against existing profiles, without a database.

The package is the single source of the mapping. This checks field names and source
anchors, not whether prose is faithful or a model will produce usable content.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import load_profiles  # noqa: E402

PROFILES = load_profiles(ROOT / "backend" / "domain" / "profiles")
BOARD_PROFILES = {"H": "cthulhu-dark-2e", "F": "daggerheart"}


def check_package(package: str) -> int:
    anchors = set(re.findall(r"^> \*\*([HF]-S\d+)\*\*", package, re.MULTILINE))
    expected_questions = {f"{board}{index}" for board in BOARD_PROFILES for index in range(1, 11)}
    seen: set[str] = set()
    for line in package.splitlines():
        if not re.match(r"^\|\s*[HF]\d+(?:-[a-z]+)?\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 6, f"question mapping must have six cells: {line}"
        row_id, _question, card_cell, field_cell, source_cell, answer = cells
        assert row_id not in seen, f"duplicate question row: {row_id}"
        seen.add(row_id)
        assert row_id.split("-")[0] in expected_questions, f"unknown question: {row_id}"
        card_match = re.fullmatch(r"`([^`]+)`", card_cell)
        assert card_match, f"{row_id}: use one explicit card type per mapping row"
        card_type = card_match[1]
        profile = PROFILES[BOARD_PROFILES[row_id[0]]]
        definition = next((item for item in profile.card_definitions if item.type == card_type), None)
        assert definition is not None, f"{row_id}: unknown card type {card_type}"
        assert re.fullmatch(r"`[^`]+`(?:\s*、\s*`[^`]+`)*", field_cell), (
            f"{row_id}: list every mapped field explicitly in backticks"
        )
        fields = re.findall(r"`([^`]+)`", field_cell)
        assert fields, f"{row_id}: no mapped fields"
        unknown = set(fields) - set(definition.required_fields) - set(definition.optional_fields)
        assert not unknown, f"{row_id}: {definition.type} does not define {sorted(unknown)}"
        refs = re.findall(r"[HF]-S\d+", source_cell)
        assert refs and all(ref in anchors and ref[0] == row_id[0] for ref in refs), (
            f"{row_id}: missing or wrong-board source anchors {source_cell}"
        )
        assert answer, f"{row_id}: missing answer boundary"
    assert expected_questions <= seen, f"missing questions: {sorted(expected_questions - seen)}"
    return len(seen)


def main() -> None:
    package = (ROOT / "docs" / "WORKFLOW_ACCEPTANCE_PACKAGE.md").read_text(encoding="utf-8")
    count = check_package(package)
    print(f"PASS: {count} actual acceptance mappings use existing fields and source anchors")


if __name__ == "__main__":
    main()
