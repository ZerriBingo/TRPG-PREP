# Maintenance: semantic ownership and transport windows

Date: 2026-09-03

## Decision

The current preparation pipeline uses the semantic segment as the unit of
logical ownership. A transport window is only a request-sized view of that
segment. It may divide a large segment, but the division must preserve source
coverage and must not create a new user-facing fact unit.

Window observations and intermediate reducer results remain internal. A
segment exposes one source-bound result only after its required transport and
consolidation work succeeds. The preparation job publishes its reviewable
results only after all selected segments have completed.

## Invariants

1. Every selected source character belongs to exactly one transport core slice.
2. A transport split does not change semantic ownership.
3. Window observations are not review records.
4. An incomplete or failed segment does not publish a partial segment result.
5. A successful segment has one final consolidation result and one review projection,
   even when internal reduction used several batches or rounds.
6. Source references and distinct observations survive batching and consolidation;
   request budgets never justify silent truncation.
7. When completed reducer batches preserve distinct valid observations within the
   public candidate limit, a deterministic no-loss final task may close the segment.
   It records its parent tasks and candidate signature; it does not make another
   model request or expose intermediate batches.
8. Every attempted segment consolidation ends in `succeeded` or `failed`; a
   segment with no reducer task remains unstarted. An outer worker exception must
   not leave an attempted segment in `running` or an ambiguous null state.

## Recovery

- Reducer state is persisted before an external model request.
- Interrupted or failed internal work remains diagnosable and retryable; successful
  transport work is reused where its inputs still match.
- Idempotency includes the model snapshot, fake/real mode, prompt version, and exact
  reducer input so different executions cannot collide silently.
- Deleting a preparation task removes its task-owned analysis tree while promoted
  source facts remain independent.

## Verification

- `uv run python scripts/test_semantic_segmentation.py`
- `uv run python scripts/test_semantic_window_integrity.py`
- `uv run python scripts/test_semantic_consolidation.py`
- `uv run python scripts/test_semantic_recovery.py`
- `uv run python scripts/test_semantic_stabilization.py`
- `uv run python scripts/test_review_batch_routing.py`
- `uv run ruff check backend scripts`
- `uv run ty check backend`
- `python -m compileall -q backend`
- `node --check frontend/workbench.js`
