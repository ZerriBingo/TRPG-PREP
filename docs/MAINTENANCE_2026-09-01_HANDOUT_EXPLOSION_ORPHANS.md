# Maintenance: handout explosion and orphan analysis windows

Date: 2026-09-01

## Diagnosis

The latest 20-page test contained 908 facts, of which 839 were auto-labelled
handout facts named with sequential neutral titles. The detector was accepting
ordinary PDF text blocks through a permissive title path and turning them into
formal facts and display materials.

The deleted project also left one `shadow_task` whose idempotency key referenced
a `prep_job_*` that no longer existed. This was an orphan analysis window.

## Changes

- Formal display materials now require an explicit source label. Generic text
  blocks and unlabeled image captions are not promoted to facts.
- Added atomic cleanup for shadow tasks whose `prep_job_*` owner is absent.
  The cleanup runs before the analysis-window list is returned.
- Removed the observed orphan task from the current local database.

## Routing

This maintenance used `diagnosing-bugs`, `domain-modeling`, `codebase-design`,
`tdd`, and `writing-for-agents`. The product decisions had already been
confirmed, so `grilling` was used only for the prior decision round and not for
new questions in this implementation turn.

## Verification

- `python scripts/test_handout_coverage.py`
- `python scripts/test_prep_job.py`
- `python scripts/test_shadow_review.py`
- `python scripts/test_review_batch_routing.py`
- `python scripts/test_runtime_material_projection.py`
- `python -m compileall -q backend scripts`
- `node --check frontend/workbench.js`
