"""Static contract for the runtime card layout and single clock entry point."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "workbench.css").read_text(encoding="utf-8")
SOURCE = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")

assert 'id="fact-grid" class="card-grid masonry-grid"' in HTML
assert 'id="work-card-grid" class="card-grid masonry-grid"' in HTML
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

print("PASS: runtime layout uses vertical columns and one interactive clock entry")
