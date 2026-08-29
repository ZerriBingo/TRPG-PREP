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

## Confirmed Direction (2026-08-29)

- The first acceptance target is a complete single-chapter workflow from uploaded PDF to usable runtime scene.
- A card's `fact_ids` form its fact closure; field sources may not cite facts outside that closure.
- Coverage has deterministic and review-required layers; model-discovered locations do not enter the runtime package automatically.
- The bookshelf-to-runtime path is primary. The facts graph remains a secondary retrieval and provenance surface.
- Supplemental generation appends reviewable cards only. It never silently regenerates the full board.
- Runtime navigation supports a non-linear location network. A started scene owns a runtime snapshot; deleting the assembled scene is the rebuild path.
