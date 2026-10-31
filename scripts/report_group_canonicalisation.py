"""Report runtime-group canonicalisation for one artifact build.

Read-only: opens the database in read-only mode, never calls a model.

The identity reconciliation stage clusters the plans it believes describe the same
recurring situation across chapters, and each cluster is ground truth for "one thing".
Counting the distinct runtime-group keys inside a cluster therefore measures exactly the
question this report exists for: does one situation keep one key, or does each chapter
invent its own?

Usage:
    python scripts/report_group_canonicalisation.py [artifact_job_id]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "app.db"


def newest_job(connection: sqlite3.Connection) -> str | None:
    """profile_id lives inside the JSON payload, not in a column."""
    for job_id, raw in connection.execute(
        "SELECT id, data FROM artifact_jobs WHERE status = 'completed' ORDER BY created_at DESC"
    ):
        if json.loads(raw).get("profile_id") == "daggerheart":
            return job_id
    return None


def main() -> None:
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    if job_id is None:
        job_id = newest_job(connection)
        if job_id is None:
            print("NO completed daggerheart artifact job found")
            return
    job_row = connection.execute(
        "SELECT data FROM artifact_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if job_row is None:
        print(f"UNKNOWN job {job_id}")
        return
    job = json.loads(job_row[0])
    workspace = job["workspace_id"]

    planned: dict[str, dict] = {}
    clusters: list[dict] = []
    for (stage, raw) in connection.execute(
        "SELECT stage, data FROM artifact_job_steps WHERE job_id = ? AND stage IN "
        "('global_plan', 'identity_reconcile') ORDER BY stage, step_index",
        (job_id,),
    ):
        output = json.loads(raw).get("output") or {}
        if stage == "global_plan":
            for card in output.get("cards") or []:
                planned[card["id"]] = {
                    "type": card.get("type"),
                    "title": card.get("title"),
                    "situation": card.get("situation"),
                    "groups": list(card.get("runtime_group_ids") or []),
                }
        else:
            clusters.extend(output.get("clusters") or [])
    bundle_row = connection.execute(
        "SELECT data FROM domain_bundles WHERE id = ?", (workspace,)
    ).fetchone()
    connection.close()
    bundle = json.loads(bundle_row[0]) if bundle_row else {}
    saved = bundle.get("cards") or []

    print(f"JOB {job_id}")
    print(f"WORKSPACE {workspace}")
    print(f"PLANNED_CARDS {len(planned)}  SAVED_CARDS {len(saved)}  CLUSTERS {len(clusters)}")

    all_ids = sorted({g for card in saved for g in (card.get("runtime_group_ids") or [])})
    placeholders = [g for g in all_ids if g.startswith("encounter_situation_")]
    print(f"DISTINCT_GROUP_IDS {len(all_ids)}  PLACEHOLDER_SLUGS {len(placeholders)}")

    env_saved = [c for c in saved if c.get("type") == "environment"]
    env_planned = [c for c in planned.values() if c["type"] == "environment"]
    print(
        "ENVIRONMENT_GROUPS planned=%s saved=%s"
        % (
            json.dumps(dict(sorted(Counter(len(c["groups"]) for c in env_planned).items()))),
            json.dumps(
                dict(
                    sorted(
                        Counter(len(c.get("runtime_group_ids") or []) for c in env_saved).items()
                    )
                )
            ),
        )
    )
    multi_chapter = sum(1 for c in saved if len(c.get("chapter_ids") or []) > 1)
    print(f"CARDS_SPANNING_MULTIPLE_CHAPTERS {multi_chapter}/{len(saved)}")

    # Ground truth: one cluster is one recurring situation.
    print("")
    print("CLUSTER_GROUP_SPREAD (distinct runtime-group keys per identity cluster)")
    spread: Counter = Counter()
    detail: list[tuple[int, str, int]] = []
    for cluster in clusters:
        members = [pid for pid in (cluster.get("plan_ids") or []) if pid in planned]
        if not members:
            continue
        keys = {g for pid in members for g in planned[pid]["groups"]}
        spread[len(keys)] += 1
        if len(keys) > 1:
            detail.append((len(keys), str(cluster.get("label") or ""), len(members)))
    print("SPREAD_HISTOGRAM " + json.dumps(dict(sorted(spread.items()))))
    resolved = spread.get(0, 0) + spread.get(1, 0)
    total = sum(spread.values())
    print(f"CLUSTERS_WITH_ONE_KEY {resolved}/{total}")

    # Normalise for cluster size: a bigger cluster has more chances to show variance,
    # so the reconcile stage's aggressiveness must not be read as naming variance.
    by_size: dict[int, Counter] = defaultdict(Counter)
    for cluster in clusters:
        members = [pid for pid in (cluster.get("plan_ids") or []) if pid in planned]
        if len(members) < 2:
            continue
        keys = {g for pid in members for g in planned[pid]["groups"]}
        by_size[len(members)][len(keys)] += 1
    print("")
    print("ONE_KEY_RATE_BY_CLUSTER_SIZE (multi-member clusters only)")
    for size in sorted(by_size):
        counter = by_size[size]
        total_size = sum(counter.values())
        one = counter.get(1, 0)
        print(
            f"    members={size} clusters={total_size} one_key={one} "
            f"rate={one / total_size:.2f} spread={json.dumps(dict(sorted(counter.items())))}"
        )
    for count, label, members in sorted(detail, reverse=True)[:15]:
        print(f"    keys={count} members={members} label={label[:70]}")

    # Variant families: keys that share a leading token often name one thing.
    stems: dict[str, list[str]] = defaultdict(list)
    for key in all_ids:
        body = re.sub(r"^encounter_", "", key)
        body = re.sub(r"_[0-9a-f]{10}$", "", body)
        stems[body.split("_")[0]].append(key)
    families = {stem: keys for stem, keys in stems.items() if len(keys) > 1}
    print("")
    print(f"SHARED_STEM_FAMILIES {len(families)}")
    for stem, keys in sorted(families.items(), key=lambda kv: -len(kv[1]))[:12]:
        print(f"    {stem} -> {json.dumps(keys, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
