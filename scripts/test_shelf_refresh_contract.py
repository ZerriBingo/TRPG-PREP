"""Regression contract for the first-promotion bookshelf refresh path."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")


helper_start = SOURCE.index("function adoptPromotedWorkspace")
helper_end = SOURCE.index("async function loadReviewQueue", helper_start)
helper = SOURCE[helper_start:helper_end]
assert "state.exampleId = workspaceId;" in helper
assert 'params.set("example", workspaceId);' in helper
assert 'history.replaceState(null, "", location.pathname + "?" + params.toString());' in helper
assert "updateSessionReviewLinks();" in helper

action_start = SOURCE.index("async function submitReviewAction")
action_end = SOURCE.index("async function submitReviewBatch", action_start)
action = SOURCE[action_start:action_end]
assert "adoptPromotedWorkspace(result.promotions);" in action

batch_start = SOURCE.index("async function submitReviewBatch")
batch_end = SOURCE.index("async function splitReviewCandidate", batch_start)
batch = SOURCE[batch_start:batch_end]
assert "let promotedWorkspaceId = null;" in batch
assert "adoptPromotedWorkspace(result.promotions)" in batch
assert "if (promotedWorkspaceId)" in batch
assert "await loadWorkspaces();" in batch
assert "await refreshWorkbenchData();" in batch

show_view_start = SOURCE.index("function showView")
show_view_end = SOURCE.index("function renderArtifactStage", show_view_start)
show_view = SOURCE[show_view_start:show_view_end]
assert 'if (viewName === "shelf" && state.exampleId)' in show_view
assert "refreshWorkbenchData().catch" in show_view

refresh_start = SOURCE.index("async function refreshWorkbenchData")
refresh_end = SOURCE.index("function renderEmptyWorkspace", refresh_start)
refresh = SOURCE[refresh_start:refresh_end]
assert "await loadSession();" in refresh
assert "renderAll();" in refresh
assert "finally" in refresh

print("PASS: first bookshelf promotion adopts workspace state before shelf navigation")
