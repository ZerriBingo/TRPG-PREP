"""Static contract for the runtime card layout and single clock entry point."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "workbench.css").read_text(encoding="utf-8")
SOURCE = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")

assert 'id="fact-grid" class="card-grid masonry-grid"' in HTML
assert 'id="work-card-grid" class="card-grid masonry-grid"' in HTML
assert 'id="card-chapter"' in HTML
assert 'id="fact-structure"' in HTML
assert 'id="runtime-reference-list" class="runtime-reference-list masonry-grid"' in HTML
overview_index = HTML.index('class="panel runtime-overview-panel"')
clocks_index = HTML.index('class="panel runtime-clocks-panel"')
exploration_index = HTML.index('class="panel runtime-exploration-panel"')
assert overview_index < clocks_index < exploration_index
assert HTML.count('id="runtime-clocks"') == 1
assert "column-count: 3" in CSS
assert "break-inside: avoid" in CSS
assert "时钟" not in HTML[exploration_index:HTML.index("id=\"scene-holder\"")]
assert "['clock', 'operation_clock', 'encounter_clock'].includes(card.type)" in SOURCE
assert 'runtimeCardFieldsHtml(clock) + sourceHtml(clock.fact_ids)' in SOURCE
assert "function localDateTime(value)" in SOURCE
assert "new Intl.DateTimeFormat" in SOURCE
assert "state.savedAt.replace" not in SOURCE
assert "state.sessionUpdatedAt.replace" not in SOURCE
assert "entry.created_at.replace" not in SOURCE
assert 'id="runtime-anchor-title"' in HTML
assert 'plan.navigation_mode === "environment"' in SOURCE
assert "function runtimeGroupHtml(anchor)" in SOURCE
assert 'runtime_brief' in SOURCE
assert "environment_card_ids" in SOURCE
assert 'data-structure-facts=' in SOURCE
assert "chapter.overview || null" in SOURCE
assert '"生成全部章节产物"' in SOURCE

print("PASS: runtime layout uses vertical columns and one interactive clock entry")
