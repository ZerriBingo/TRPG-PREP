# Architecture Review - 2026-08-28 (historical)

Status: superseded by the new-project boundary recorded on 2026-08-30

## Decision

Pause feature expansion until the job lifecycle and domain persistence seams are explicit and observable.

## Existing Flow

Upload/source analysis -> candidate review -> promoted facts -> artifact job -> validated cards -> bookshelf -> runtime scene assembly.

## Findings

Supplemental jobs previously entered the full-generation duplicate guard, and the UI did not start durable polling after enqueue. That implementation is historical; the supplemental path has since been deleted.

## Next Gates

1. Keep HTTP coverage for full-board enqueue, polling, failure, and retry.
2. Render phase and card progress consistently.
3. Continue the coverage audit, facts graph, and runtime navigation only after the new-project path remains stable.
