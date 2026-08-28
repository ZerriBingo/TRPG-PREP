# ADR: Prep Workflow Boundaries

Date: 2026-08-28
Status: accepted for implementation

## Decisions

- Supplemental location cards are incremental jobs. They append only new, uncovered evidence and never replace the existing board.
- Coverage audit is evidence-based and reports uncovered location facts; it must not invent a linear route.
- The facts graph is a retrieval and provenance view, not the primary runtime surface.
- Runtime mode owns current scene, revealed clues, and exploratory locations. Scene assembly is rebuildable by deleting the assembled plan.
- Every background job exposes durable phase/status and must be visible through polling.

## Rejected Alternatives

- Re-running the complete artifact set for one missing location.
- Treating page boundaries as semantic scene boundaries.
- Requiring users to edit JSON to repair a scene plan.

## Open Questions

- Whether coverage should promote an entity index in addition to fact-kind matching.
- Which automatic graph layout remains usable on mobile without persisted coordinates.
