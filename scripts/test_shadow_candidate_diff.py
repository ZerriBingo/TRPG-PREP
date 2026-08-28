"""Offline regression checks for the stable P1.2 shadow-candidate diff."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain.shadow_diff import (  # noqa: E402
    build_diff_report,
    load_candidate_snapshot,
    load_gold_standard,
    render_diff_markdown,
)
from backend.app import shadow as shadow_service  # noqa: E402
from backend.app import storage  # noqa: E402
from backend.domain.shadow import ShadowTaskSpec  # noqa: E402
from diff_shadow_candidates import load_task_candidates  # noqa: E402


def assert_task_id_path(gold: object) -> None:
    """Exercise the CLI helper against a temporary isolated shadow database."""
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "shadow-candidate-diff.db"
        try:
            storage.init_db()
            task, created = shadow_service.create_shadow_task(
                ShadowTaskSpec(
                    idempotency_key="p1.2-task-id-fixture",
                    source_file="Resource/奈亚拉托提普的面具v1.0.pdf",
                    source_version="v1.0",
                    source_pages=[159],
                    profile_id="cthulhu-dark-2e",
                    model_id="offline-task-id-fixture",
                    prompt_version="p1.2-test",
                    schema_version="shadow-candidate-v1",
                    input_excerpt="A short offline source excerpt.",
                )
            )
            assert created is True
            _, run, produced = shadow_service.submit_shadow_result(
                task.id,
                raw_response=json.dumps(
                    {
                        "candidates": [
                            {
                                "text": "咀咀屋位于兰桑姆1号小院，院内还有废弃当铺。",
                                "kind": "location",
                                "source_refs": [
                                    {
                                        "file": "Resource/奈亚拉托提普的面具v1.0.pdf",
                                        "page": 159,
                                        "locator": "恐怖的咀咀屋",
                                    }
                                ],
                                "possible_links": ["fact_naimen_juju_location"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            )
            assert run.status == "succeeded"
            assert len(produced) == 1

            stored_candidates, metadata = load_task_candidates(task.id)
            assert len(stored_candidates) == 1
            assert stored_candidates[0].id == produced[0].id
            assert metadata["snapshot_source"] == "sqlite_shadow_task"
            report = build_diff_report(gold, stored_candidates, snapshot_metadata=metadata)
            assert report["summary"]["covered_fact_count"] == 1
        finally:
            storage.DB_PATH = original_db_path


def main() -> None:
    gold = load_gold_standard(
        ROOT / "data" / "fixtures" / "naimen_juju_house_unit.json",
        ROOT / "backend" / "domain" / "examples" / "naimen_pilot.json",
        ROOT / "backend" / "domain" / "profiles",
    )
    candidates, metadata = load_candidate_snapshot(
        ROOT / "data" / "fixtures" / "naimen_shadow_candidate_diff_fixture.json"
    )

    first = build_diff_report(gold, candidates, snapshot_metadata=metadata)
    second = build_diff_report(gold, candidates, snapshot_metadata=metadata)
    assert first == second
    assert first["baseline"]["fact_count"] == 20
    assert first["summary"]["covered_fact_count"] == 5
    assert first["summary"]["missing_item_count"] == 15
    assert first["summary"]["unsupported_source_page_count"] == 1
    assert first["summary"]["wrong_page_number_count"] == 1
    assert first["summary"]["wrong_merge_count"] == 1
    assert first["summary"]["over_summary_count"] >= 2
    assert first["summary"]["type_mismatch_count"] >= 2
    assert first["unsupported_source_pages"][0]["source_ref"]["page"] == 166
    assert first["wrong_page_numbers"][0]["fact_id"] == "fact_naimen_enkowan_front"
    assert first["wrong_merges"][0]["candidate_id"] == "shadow_candidate_fixture_003"

    markdown = render_diff_markdown(first)
    for heading in (
        "## 漏项",
        "## 无依据页或版本",
        "## 错误页码",
        "## 疑似错误合并",
        "## 疑似过度摘要",
    ):
        assert heading in markdown
    assert first["baseline"]["fingerprint"] in markdown
    assert first["candidate_snapshot"]["fingerprint"] in markdown
    assert_task_id_path(gold)
    print("PASS: shadow candidate diff is stable and reports all five planned classes")


if __name__ == "__main__":
    main()
