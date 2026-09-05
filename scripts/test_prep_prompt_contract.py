"""Contract for semantic-window candidate classification guidance."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import prep  # noqa: E402
from backend.domain import ExtractionWindow, PageSpan, PrepJob, PrepScope  # noqa: E402


job = PrepJob(
    id="prep_job_prompt_contract",
    scope=PrepScope(
        source_file="fixture://prompt",
        source_version="v1",
        page_spans=[PageSpan(start=1, end=2)],
        profile_id="cthulhu-dark-2e",
        objective="整理事实",
    ),
    model_id="fixture",
    prompt_version=prep.PROMPT_VERSION,
    schema_version=prep.SCHEMA_VERSION,
    segmentation_strategy="semantic-v2",
    segmentation_status="succeeded",
    semantic_segments=[PageSpan(start=1, end=2, label="测试语义段")],
    windows=[
        ExtractionWindow(
            id="prep_window_prompt_contract",
            page_span=PageSpan(start=1, end=2),
            core_span=PageSpan(start=1, end=2),
            semantic_segment_id="semantic_segment_prompt_contract_1",
            segment_window_index=1,
            segment_window_count=1,
            core_text_slices=[],
        )
    ],
    created_at="2026-09-03T00:00:00+00:00",
    updated_at="2026-09-03T00:00:00+00:00",
)
messages = prep._prompt_messages(job, job.windows[0], "--- PDF p1 ---\n地点与人物")
system = messages[0]["content"]
assert prep.PROMPT_VERSION == "prep-fact-extract-v5"
for kind in (
    "clue",
    "npc",
    "location",
    "handout",
    "event",
    "threat",
    "stakes",
    "obstacle",
    "timeline",
    "resource",
):
    assert kind in system
assert "classify" in system.lower() or "分类" in system
assert "handout" in system.lower() and "location" in system.lower()
print("PASS: semantic extraction prompt carries explicit candidate taxonomy")
