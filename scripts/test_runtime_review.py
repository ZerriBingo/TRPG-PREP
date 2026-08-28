"""Regression checks for P0.3 structured GM runtime review exports."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "domain"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import (  # noqa: E402
    ExampleBundle,
    SessionLogEntry,
    SessionState,
    build_session_review,
    draft_scene_plan,
    export_session_review_markdown,
    load_json,
    load_profiles,
    validate_bundle,
    validate_session,
)


def event(
    event_id: str,
    kind: str,
    text: str,
    created_at: str,
    *,
    plan_id: str,
    card_id: str,
    beat_id: str,
    subject_type: str | None = None,
    subject_id: str | None = None,
    metadata: dict[str, str | int | float | bool] | None = None,
) -> SessionLogEntry:
    return SessionLogEntry(
        id=event_id,
        kind=kind,
        text=text,
        created_at=created_at,
        plan_id=plan_id,
        card_id=card_id,
        beat_id=beat_id,
        subject_type=subject_type,
        subject_id=subject_id,
        metadata=metadata or {},
    )


def main() -> None:
    profiles = load_profiles(DOMAIN / "profiles")
    bundle = ExampleBundle.model_validate(
        load_json(DOMAIN / "examples" / "naimen_pilot.json")
    )
    plan = draft_scene_plan(
        bundle,
        "cthulhu-dark-2e",
        [card.id for card in bundle.cards],
        "奈面复盘测试",
        "Resource/奈亚拉托提普的面具v1.0.pdf",
        [159, 160, 161, 162, 163, 164, 165],
        "用结构化运行事件验证复盘输出。",
        profile=profiles["cthulhu-dark-2e"],
    )
    bundle.plans = [plan]
    validate_bundle(bundle, profiles)

    scene_id = "card_naimen_juju_scene"
    clock_id = "card_naimen_ritual_clock"
    beat_id = plan.beats[0].id
    session = SessionState(
        example_id=bundle.id,
        current_plan_id=plan.id,
        current_beat_id=beat_id,
        current_card_id=scene_id,
        revealed_clue_keys=[scene_id + ":direct:0"],
        clock_stages={clock_id: 1, "card_naimen_exposure_clock": 0},
        log=[
            event(
                "log_001",
                "run_started",
                "开始场景计划：奈面复盘测试",
                "2026-08-26T09:00:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="session",
                subject_id=plan.id,
            ),
            event(
                "log_002",
                "lookup",
                "查找卡片：咀咀屋",
                "2026-08-26T09:01:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="card",
                subject_id=scene_id,
                metadata={"subject_type": "card"},
            ),
            event(
                "log_003",
                "source_page_opened",
                "打开来源页：Resource/奈亚拉托提普的面具v1.0.pdf · p159",
                "2026-08-26T09:02:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="source_page",
                subject_id="Resource/奈亚拉托提普的面具v1.0.pdf:p159",
                metadata={
                    "file": "Resource/奈亚拉托提普的面具v1.0.pdf",
                    "page": 159,
                    "reason": "source_reference",
                },
            ),
            event(
                "log_004",
                "clue_revealed",
                "揭示线索：居民确认深夜有陌生人和怪声",
                "2026-08-26T09:03:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="clue",
                subject_id=scene_id + ":direct:0",
                metadata={"clue_key": scene_id + ":direct:0"},
            ),
            event(
                "log_005",
                "clock_advanced",
                "推进时钟：月朔仪式 → 院中三人假扮酒鬼",
                "2026-08-26T09:04:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="clock",
                subject_id=clock_id,
                metadata={"from_stage": 0, "to_stage": 1, "stage_title": "院中三人假扮酒鬼"},
            ),
            event(
                "log_006",
                "field_edited",
                "手工改写字段：card.fields.gm_moves",
                "2026-08-26T09:05:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="field",
                subject_id="card:" + scene_id,
                metadata={
                    "entity_type": "card",
                    "entity_id": scene_id,
                    "field_path": "fields.gm_moves",
                },
            ),
            event(
                "log_007",
                "lookup_missing",
                "未找到：罗伯森如何回应直接证据",
                "2026-08-26T09:06:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="session",
                subject_id="lookup_gap",
            ),
            event(
                "log_008",
                "manual_note",
                "需要在下一次准备前核对警方入口。",
                "2026-08-26T09:07:00+00:00",
                plan_id=plan.id,
                card_id=scene_id,
                beat_id=beat_id,
                subject_type="session",
            ),
        ],
    )
    validate_session(session, bundle)

    review = build_session_review(session, bundle)
    assert review["summary"]["event_count"] == 8
    assert review["summary"]["lookup_count"] == 1
    assert review["summary"]["lookup_gap_count"] == 1
    assert review["summary"]["source_page_open_count"] == 1
    assert review["summary"]["revealed_clue_count"] == 1
    assert review["summary"]["clock_change_count"] == 1
    assert review["summary"]["field_edit_count"] == 1
    assert review["source_pages"] == [
        {
            "file": "Resource/奈亚拉托提普的面具v1.0.pdf",
            "page": 159,
            "open_count": 1,
            "first_opened_at": "2026-08-26T09:02:00+00:00",
            "last_opened_at": "2026-08-26T09:02:00+00:00",
        }
    ]
    assert review["field_edits"][0]["metadata"]["field_path"] == "fields.gm_moves"
    assert review["card_attention"][0]["card_id"] == scene_id

    markdown = export_session_review_markdown(session, bundle)
    assert "未找到的信息" in markdown
    assert "罗伯森如何回应直接证据" in markdown
    assert "手工补写与字段改动" in markdown
    print("PASS: structured runtime events produce a compact review")

    legacy = SessionLogEntry(
        id="log_legacy_note",
        kind="note",
        text="Legacy session notes remain readable.",
        created_at="2026-08-26T09:08:00+00:00",
    )
    assert legacy.kind == "note"
    print("PASS: legacy session log entries remain compatible")


if __name__ == "__main__":
    main()
