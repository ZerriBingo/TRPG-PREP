"""Regression contract for bounded artifact-job question summaries."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.artifacts import _OpenQuestionSummary, _question_summary_fields  # noqa: E402
from backend.domain import ArtifactDraftJob  # noqa: E402


summary = _OpenQuestionSummary()
summary.add([f"待确认问题 {index}" for index in range(81)])
fields = _question_summary_fields(summary)
assert len(fields["open_questions"]) == 50
assert fields["open_question_count"] == 81
assert fields["open_question_overflow_count"] == 31

job = ArtifactDraftJob.model_validate({
    "id": "artifact_job_question_summary",
    "workspace_id": "question_summary_workspace",
    "profile_id": "module-prep",
    "model_id": "fixture",
    "status": "running",
    "phase": "materializing",
    "created_at": "2026-09-03T00:00:00+00:00",
    "updated_at": "2026-09-03T00:00:00+00:00",
    **fields,
})
assert job.open_question_count == 81
assert job.open_question_overflow_count == 31

legacy = ArtifactDraftJob.model_validate({
    "id": "artifact_job_legacy_questions",
    "workspace_id": "question_summary_workspace",
    "profile_id": "module-prep",
    "model_id": "fixture",
    "created_at": "2026-09-03T00:00:00+00:00",
    "updated_at": "2026-09-03T00:00:00+00:00",
    "open_questions": ["旧任务的问题"],
})
assert legacy.open_question_count == 1
assert legacy.open_question_overflow_count == 0
print("PASS: artifact jobs retain a bounded question preview with full counts")
