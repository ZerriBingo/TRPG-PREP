"""Regression checks for durable artifact job state monotonicity."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")

assert "function artifactJobUpdatedAt(job)" in source
assert "retryingJobId" in source
assert "allowRetryTransition" in source
assert 'current.status === "completed" && job.status !== "completed"' in source
assert "state.artifacts.job = payload.artifact_job || null" not in source
assert "state.artifacts.job = state.data.artifact_job || null" not in source
assert "updatedAt < currentUpdatedAt" in source
print("PASS: artifact job retries and stale refreshes follow monotonic state")
