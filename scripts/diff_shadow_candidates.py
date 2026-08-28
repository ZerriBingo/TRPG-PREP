"""Render a stable P1.2 diff between shadow candidates and the manual gold standard."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.domain.shadow import ShadowCandidate  # noqa: E402
from backend.domain.shadow_diff import (  # noqa: E402
    ShadowDiffError,
    build_diff_report,
    load_candidate_snapshot,
    load_gold_standard,
    render_diff_markdown,
)


DEFAULT_GOLD_UNIT = ROOT / "data" / "fixtures" / "naimen_juju_house_unit.json"
DEFAULT_PILOT = ROOT / "backend" / "domain" / "examples" / "naimen_pilot.json"
DEFAULT_PROFILES = ROOT / "backend" / "domain" / "profiles"


def load_task_candidates(task_id: str) -> tuple[list[ShadowCandidate], dict[str, Any]]:
    """Read only queued review candidates from the isolated shadow tables."""
    try:
        task = storage.load_shadow_task(task_id)
    except sqlite3.Error as error:
        raise ShadowDiffError(
            "could not read shadow tables; start the workbench once before using --task-id"
        ) from error
    if task is None:
        raise ShadowDiffError(f"unknown shadow task: {task_id}")
    try:
        candidates = [
            ShadowCandidate.model_validate(candidate)
            for candidate in storage.list_shadow_candidates(task_id, "needs_review")
        ]
    except ValueError as error:
        raise ShadowDiffError(f"stored candidates for {task_id} are invalid: {error}") from error
    metadata = {
        "snapshot_source": "sqlite_shadow_task",
        "id": task["id"],
        "source_file": task["source_file"],
        "source_version": task["source_version"],
        "source_pages": sorted(task["source_pages"]),
        "profile_id": task["profile_id"],
        "model_id": task["model_id"],
        "prompt_version": task["prompt_version"],
        "schema_version": task["schema_version"],
    }
    return sorted(candidates, key=lambda candidate: candidate.id), metadata


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--candidates",
        type=Path,
        help="API-shaped JSON snapshot containing a candidates list",
    )
    source_group.add_argument(
        "--task-id",
        help="isolated shadow task id to read from data/app.db",
    )
    parser.add_argument("--gold-unit", type=Path, default=DEFAULT_GOLD_UNIT)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--write-json", type=Path)
    parser.add_argument("--write-markdown", type=Path)
    parser.add_argument("--text-match-threshold", type=float, default=0.55)
    parser.add_argument("--field-length-ratio-limit", type=float, default=0.55)
    args = parser.parse_args()

    try:
        gold = load_gold_standard(args.gold_unit, args.pilot, args.profiles)
        if args.candidates is not None:
            candidates, metadata = load_candidate_snapshot(args.candidates)
        else:
            candidates, metadata = load_task_candidates(args.task_id)
        report = build_diff_report(
            gold,
            candidates,
            snapshot_metadata=metadata,
            text_match_threshold=args.text_match_threshold,
            field_length_ratio_limit=args.field_length_ratio_limit,
        )
    except ShadowDiffError as error:
        parser.error(str(error))

    markdown = render_diff_markdown(report)
    if args.write_json:
        write_json(args.write_json, report)
    if args.write_markdown:
        args.write_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.write_markdown.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
