# Reassessment - 2026-08-29 (historical)

This record predates the 2026-08-30 new-project boundary. Statements about incremental cards, bounded rebuilds, or preserving old artifact paths are historical and are superseded by the current boundary document.

## Confirmed

- The primary user journey is bookshelf project -> current prep version -> runtime scene.
- Reality-horror is the first board with a complete single-chapter runtime acceptance gate.
- Other boards need separate acceptance criteria rather than inheriting the reality-horror standard.
- Runtime locations are narrative-operational units. A place may deserve its own scene treatment even when its immediate purpose is obtaining one piece of evidence.
- The facts graph is secondary retrieval/provenance; it must not be the only place where runtime-critical location context exists.
- Supplemental generation was once considered incremental and append-only; that path is now deleted.
- A shared task-center model is preferred for all long-running jobs.
- Legacy data deletion is favored in principle but not yet scoped or authorized.
- Seed instances are confirmed as development-only data and should be removed from formal user data while repository fixtures remain.
- Fantasy/adventure combat support should be environment-led: the environment card can carry the primary scene operation, with concise adversary/support cards beside it.
- Fantasy combat scenes are grouped runtime units with an environment card at the center, optional adversary/support cards, and optional clocks or phases.
- City-scale fantasy content uses a situation hub plus independently searchable recurring entities; minor one-off content can stay in a beat or hub.
- Travel/transition material is optional and detour-friendly, not a mandatory route.
- Source statistics are preserved as citations; cross-board conversions are suggestions requiring GM review.
- Seed cleanup is scoped to explicit fixture IDs and must not affect user-owned historical versions.
- The task center is a shared surface for all durable jobs and their replace impact.
- New-project behavior takes precedence over historical data. Old projects, artifacts, links, and jobs are not migrated, rewritten, or repaired for compatibility.
- The old supplemental/"补卡" workflow is deleted. New projects report uncovered source material inline; correction means a new explicit preparation task instead of a supplemental job.
- Display materials are source-page records shared by all boards. They retain titles, page ranges, associations, and GM display notes; the tool does not crop, upload crops, or ask the LLM to rewrite player-facing content.
- The facts graph is a read-only project navigation and provenance view. It uses typed, evidence-backed relationships, local focus with progressive expansion, a list fallback, and no user-saved layout.

## Deferred Until Next Grill Round

- Board-specific acceptance gates for fantasy/adventure and general prep.
- The minimum independent scene treatment for small or evidence-only locations.
- Exact legacy data deletion scope, backup policy, and timing.
- Task-center placement and the user-visible progress contract.
- Fantasy combat card contracts: environment-led scene fields, adversary companion fields, and how travel/transition beats attach to a chapter.

## Final Implementation Order

1. Add public-seam regression coverage for full-board jobs: enqueue, durable polling, failure detail, and retry.
2. Fix the shared task lifecycle and render phase/progress consistently in the UI.
3. Remove formal seed instances by explicit fixture IDs and prevent future automatic seeding; retain repository fixtures.
4. Introduce a derived location/entity coverage index without requiring manual entity maintenance.
5. Update reality-horror artifact generation so runtime-relevant locations receive independent, context-rich scene treatment.
6. Add fantasy/adventure classification for out-of-combat narrative, environment-led combat groups, adversary/support cards, and travel transitions.
7. Prototype and Playwright-test the coverage-warning-to-new-task-to-runtime flow before any broad visual redesign.
8. Reassess historical-version migration only after the new single-chapter acceptance gate passes.

## Stop Conditions

- Do not add new board types or rule-specific numeric conversions before the reality-horror chapter gate passes.
- Do not silently append or partially regenerate a board from a retired supplemental request.
- Do not delete user-owned historical analysis versions automatically.
- Do not claim product completion from fixture or API-only tests; the real browser workflow must pass.

## Final Grill Closure (2026-08-30)

- A new-project workspace is either newly created or explicitly re-analysed as a fresh workspace. Historical workspaces only support viewing, export, or user-initiated deletion.
- No old supplemental-card entry point remains in the new workbench. Coverage warnings do not enqueue jobs.
- Display material recognition is conservative and reviewable. A confirmed display material is an independent source-page item and never enters location coverage or location-card generation.
- Source references may contain continuous or discontinuous page ranges. The UI shows copyable file/page text and leaves external PDF viewing and cropping to the GM.
- Display materials may be associated with multiple scenes and beats. Runtime shows only the current scene's confirmed references and preserves an unassociated global list.
- The graph includes facts, people, locations, scenes, beats, display materials, and artifacts as typed nodes. It is read-only, locally focused, progressively expanded, and backed by a list/details view.
- Old untyped links stay old data and are not rewritten. New relationships are generated from the new model; structural links are deterministic, semantic links are reviewable, and only confirmed links affect runtime navigation.

## Maintenance Checkpoint (2026-08-29)

- The artifact task center now exposes only full-board jobs, with compatible retry matching and conflict responses for incompatible active jobs.
- The artifact panel exposes a visible progress bar and keeps the durable full-board job phase/error available through polling.
- Persisted development seed rows are removed at service startup by explicit fixture IDs through an atomic storage cleanup; repository fixtures remain test-only.
- The known real upstream response was replayed locally: its corrected second attempt now passes fact-closure validation. The malformed first-attempt clock shape remains fail-closed and is intentionally left to the retry/correction path.

## Maintenance Verification Addendum (2026-08-29)

- Durable full-board artifact history is a first-class workbench response (`artifact_jobs`); invalid historical incremental records are ignored.
- Public profile payloads are contract-shaped and neutral. Internal profile keys remain only as map keys for client joins; source profile metadata is withheld from the browser and rendered copy.
- Empty project selection is a supported state. No endpoint or static link silently chooses a development fixture.
- The remaining real-user artifact failure is an upstream rate-limit response. It is recorded as a retryable task state, not treated as a schema or data-loss failure.
