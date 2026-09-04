"""Regression contract for one-step candidate review promotion routing."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
start = SOURCE.index("async function submitReviewBatch()")
end = SOURCE.index("async function splitReviewCandidate", start)
batch = SOURCE[start:end]

assert "const selectedPrepJobId = prepReviewJobId(state.review.taskId);" in batch
assert "requestGroups.set(`prep:${selectedPrepJobId}`, candidateIds.slice());" in batch
assert 'fetch("/api/domain/prep/jobs", {cache: "no-store"})' in batch
assert "无法确认候选所属任务" in batch
assert "const prepJob = prepJobForShadowTask(candidate?.task_id);" not in batch
assert 'const key = prepJobId ? `prep:${prepJobId}` : "shadow";' in batch
assert "await loadReviewQueue({waitForExisting: true});" in SOURCE
assert "for (let attempt = 0; attempt < 200 && state.review.loading; attempt += 1)" in SOURCE

prep_render_start = SOURCE.index("function renderPrep()")
prep_render_end = SOURCE.index("async function loadPrepConfig", prep_render_start)
prep_render = SOURCE[prep_render_start:prep_render_end]
assert "const windowAction = window.shadow_task_id && window.candidate_count" not in prep_render
assert "window.consolidation_status === \"succeeded\"" in prep_render
assert "window.segment_window_index === window.segment_window_count" in prep_render
assert "window.consolidation_candidate_count > 0" in prep_render
assert "job.status === \"completed\"" in prep_render

print("PASS: review batch routes prep candidates through promotion endpoint")
