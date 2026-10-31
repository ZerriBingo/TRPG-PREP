"""Report the long-form artifact gate's measurable criteria for one build.

Read-only: opens the database in read-only mode and calls no model.

Grouped by the criteria in `IMPLEMENTATION_BACKLOG.md`'s `## Long-Form Artifact Gate`:

  1 Completeness — dispositions and field provenance
  2 Retrievability — questions per card, array field sizes, environment rhythm
  4 System boundary — cards rejected for a missing target-system field

Criterion 3, honesty, cannot be judged here: the validator only checks that a cited
fact ID exists, not that the fact supports the claim, so it needs the ten-card sample
read described in the gate.

Usage:
    python scripts/report_artifact_quality.py [workspace_id]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"

# Arrays that carry table-side content, as opposed to structural bookkeeping.
CONTENT_ARRAYS = ("usable_features", "impulses", "hazards", "triggers", "phase_changes", "hints")
RHYTHMS = ("探索", "社交", "穿越", "事件")


def newest_workspace(connection: sqlite3.Connection) -> str | None:
    for workspace, raw in connection.execute(
        "SELECT id, data FROM domain_bundles ORDER BY updated_at DESC"
    ):
        bundle = json.loads(raw)
        if "daggerheart" in (bundle.get("profile_ids") or []) and bundle.get("cards"):
            return workspace
    return None


def main() -> None:
    workspace = sys.argv[1] if len(sys.argv) > 1 else None
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    if workspace is None:
        workspace = newest_workspace(connection)
        if workspace is None:
            print("NO daggerheart bundle with cards found")
            return
    row = connection.execute(
        "SELECT data FROM domain_bundles WHERE id = ?", (workspace,)
    ).fetchone()
    if row is None:
        print(f"UNKNOWN workspace {workspace}")
        return
    bundle = json.loads(row[0])
    connection.close()

    facts = bundle.get("facts") or []
    dispositions = {item.get("fact_id") for item in bundle.get("fact_dispositions") or []}
    cards = bundle.get("cards") or []

    print(f"WORKSPACE {workspace}")
    print(f"FACTS {len(facts)}  DISPOSITIONS {len(dispositions)}  CARDS {len(cards)}")
    print("CARD_TYPES " + json.dumps(dict(Counter(c.get("type") for c in cards)), ensure_ascii=False))

    print("")
    print("--- 1. Completeness ---")
    undisposed = [f["id"] for f in facts if f["id"] not in dispositions]
    print(f"FACTS_WITHOUT_DISPOSITION {len(undisposed)}")
    total_fields = 0
    missing_source_key = 0
    empty_source_list: Counter = Counter()
    for card in cards:
        sources = card.get("field_sources") or {}
        for name in (card.get("fields") or {}):
            total_fields += 1
            if name not in sources:
                missing_source_key += 1
            elif not sources[name]:
                empty_source_list[name] += 1
    print(f"FIELDS {total_fields}  FIELDS_WITHOUT_SOURCE_KEY {missing_source_key}")
    print(
        "FIELDS_WITH_EMPTY_SOURCE_LIST "
        + json.dumps(dict(sorted(empty_source_list.items())), ensure_ascii=False)
    )

    print("")
    print("--- 2. Retrievability ---")
    questions = [q for card in cards for q in (card.get("open_questions") or [])]
    print(f"QUESTIONS {len(questions)}  PER_CARD {len(questions) / max(len(cards), 1):.2f}")
    array_sizes: dict[str, Counter] = {}
    array_longest: dict[str, int] = {}
    for card in cards:
        for name in CONTENT_ARRAYS:
            value = (card.get("fields") or {}).get(name)
            if isinstance(value, list) and value:
                array_sizes.setdefault(name, Counter())[len(value)] += 1
                longest = max(len(item) if isinstance(item, str) else 0 for item in value)
                array_longest[name] = max(array_longest.get(name, 0), longest)
    for name in sorted(array_sizes):
        sizes = array_sizes[name]
        print(
            f"    {name}: cards={sum(sizes.values())} sizes={json.dumps(dict(sorted(sizes.items())))} "
            f"longest_entry={array_longest.get(name, 0)}"
        )
    rhythms = [
        (card.get("fields") or {}).get("environment_type") or ""
        for card in cards
        if card.get("type") == "environment"
    ]
    led = sum(1 for value in rhythms if value.strip().startswith(RHYTHMS))
    print(f"ENVIRONMENT_TYPE rhythm_led {led}/{len(rhythms)}")
    multi = sum(1 for c in cards if len(c.get("chapter_ids") or []) > 1)
    print(f"CARDS_SPANNING_MULTIPLE_CHAPTERS {multi}/{len(cards)}")

    print("")
    print("--- 4. System boundary ---")
    profile_required = Counter()
    profiles_dir = ROOT / "backend" / "domain" / "profiles"
    for path in sorted(profiles_dir.glob("*.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))
        for card_type in definition.get("card_definitions") or []:
            for name in card_type.get("required_fields") or []:
                profile_required[f"{definition['id']}:{name}"] += 1
    # Compare whole name segments: "damaged_behavior" is a narrative field and must not
    # be flagged by the "damage" token.
    system_tokens = {"tier", "difficulty", "hp", "stress", "damage", "attack", "thresholds"}
    numeric_like = [
        name
        for name in profile_required
        if set(name.split(":", 1)[-1].split("_")) & system_tokens
    ]
    print(f"PROFILE_REQUIRED_FIELDS {len(profile_required)}  NUMERIC_OR_SYSTEM_LIKE {len(numeric_like)}")
    if numeric_like:
        print("    " + json.dumps(numeric_like, ensure_ascii=False))
    print("CARD_REJECTIONS_NOT_VISIBLE_HERE — read artifact_jobs.error for a stalled build")


if __name__ == "__main__":
    main()
