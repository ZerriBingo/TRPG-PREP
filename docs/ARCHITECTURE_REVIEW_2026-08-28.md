# Architecture Review - 2026-08-28

## Decision

Pause feature expansion until the job lifecycle and domain persistence seams are explicit and observable.

## Existing Flow

Upload/source analysis -> candidate review -> promoted facts -> artifact job -> validated cards -> bookshelf -> runtime scene assembly.

## Findings

Supplemental jobs previously entered the full-generation duplicate guard, and the UI did not start durable polling after enqueue. Both are now tracked as regression gates.

## Next Gates

1. Add HTTP regression coverage for supplemental enqueue, polling, failure, and append.
2. Render phase and card progress consistently.
3. Grill coverage audit, facts graph, and runtime navigation before redesign.
