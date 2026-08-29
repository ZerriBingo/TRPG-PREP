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

## Additional Clarifications (2026-08-29)

- The complete single-chapter runtime acceptance gate is specific to the reality-horror board. Fantasy/adventure and general-prep boards must not inherit that gate without their own decision.
- Location coverage is narrative and operational, not merely evidentiary. A place where players may investigate, return, seek help, or create a consequential follow-up should be independently visible in runtime mode. Small evidence-oriented places may use a compact scene treatment, but must not disappear into an off-screen fact list.
- A unified task-center model is part of the product direction: analysis, review, artifact, and supplemental jobs should share observable lifecycle semantics.
- Deleting legacy data remains an unresolved migration decision; no destructive cleanup is authorized by this clarification alone.

## Board-Specific Clarification (2026-08-29)

- Fantasy/adventure preparation has three functional bands: out-of-combat narrative, in-combat scenes, and travel/transitions. Card density follows the selected source and play scope rather than a fixed per-page or per-scene count.
- The supplied homebrew reference is used only as an organizational reference: concise principles, setting distinctions, an inciting situation, locations, GM guidance, and compact encounter support. Numeric balance guidance and named system mechanics are outside this product contract.
- Seed projects are development fixtures, not product content. Formal bookshelf cleanup should remove seed instances while retaining repository fixtures for automated tests.
- User-managed legacy analysis versions remain a separate, manual deletion decision.
