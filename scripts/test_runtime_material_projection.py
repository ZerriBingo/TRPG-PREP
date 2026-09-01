"""Regression contract for location-led runtime material projection."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")

location_start = SOURCE.index("function locationMaterialsHtml")
location_end = SOURCE.index("function changeTriggerState", location_start)
location = SOURCE[location_start:location_end]
assert "material.source_fact_ids" not in location
assert "link.plan_id === plan.id && link.card_id === card.id" in location

reference_start = SOURCE.index("function renderRuntimeReferenceCards")
reference_end = SOURCE.index("function fillSelectors", reference_start)
reference = SOURCE[reference_start:reference_end]
assert "materials.map" not in reference

assert "<summary>复核历史" not in SOURCE
assert '$("beat-holder").innerHTML = \'\';' in SOURCE

print("PASS: runtime materials are location-contextual and review history is hidden")
