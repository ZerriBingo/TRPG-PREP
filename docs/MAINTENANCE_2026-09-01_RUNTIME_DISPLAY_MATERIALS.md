# Maintenance: runtime display-material projection

Date: 2026-09-01

## Routing

This maintenance used `diagnosing-bugs`, `domain-modeling`, `codebase-design`,
`tdd`, `playwright` (browser-flow route retained for final verification), and
`writing-for-agents`. `grilling` was not used because the product decision was
already explicit in the request.

## Decisions

- A display material is shown in the current location when its source fact is
  part of that location, even when no explicit runtime link was recorded.
- Generic runtime reference cards do not contain display materials.
- Unassociated display materials remain available in the dedicated list.
- Location navigation does not render an empty beat placeholder.
- Review history remains stored for promotion/audit invariants but is not shown
  as a user-facing review control.
- Bare detector labels such as `地图` or `照片` receive a neutral fallback
  title such as `无注释展示资料000001`; meaningful source labels are retained.

## Verification

- `python scripts/test_runtime_material_projection.py`
- `python scripts/test_handout_coverage.py`
- `node --check frontend/workbench.js`
- `python -m compileall -q backend scripts`
