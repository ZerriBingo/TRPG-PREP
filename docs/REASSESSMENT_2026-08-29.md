# Reassessment - 2026-08-29

## Confirmed

- The primary user journey is bookshelf project -> current prep version -> runtime scene.
- Reality-horror is the first board with a complete single-chapter runtime acceptance gate.
- Other boards need separate acceptance criteria rather than inheriting the reality-horror standard.
- Runtime locations are narrative-operational units. A place may deserve its own scene treatment even when its immediate purpose is obtaining one piece of evidence.
- The facts graph is secondary retrieval/provenance; it must not be the only place where runtime-critical location context exists.
- Supplemental generation is incremental, reviewable, and append-only with respect to the existing artifact set.
- A shared task-center model is preferred for all long-running jobs.
- Legacy data deletion is favored in principle but not yet scoped or authorized.
- Seed instances are confirmed as development-only data and should be removed from formal user data while repository fixtures remain.
- Fantasy/adventure combat support should be environment-led: the environment card can carry the primary scene operation, with concise adversary/support cards beside it.
- Fantasy combat scenes are grouped runtime units with an environment card at the center, optional adversary/support cards, and optional clocks or phases.
- City-scale fantasy content uses a situation hub plus independently searchable recurring entities; minor one-off content can stay in a beat or hub.
- Travel/transition material is optional and detour-friendly, not a mandatory route.
- Source statistics are preserved as citations; cross-board conversions are suggestions requiring GM review.
- Seed cleanup is scoped to explicit fixture IDs and must not affect user-owned historical versions.
- The task center is a shared surface for all durable jobs and their append/replace impact.

## Deferred Until Next Grill Round

- Board-specific acceptance gates for fantasy/adventure and general prep.
- The minimum independent scene treatment for small or evidence-only locations.
- Exact legacy data deletion scope, backup policy, and timing.
- Task-center placement and the user-visible progress contract.
- Fantasy combat card contracts: environment-led scene fields, adversary companion fields, and how travel/transition beats attach to a chapter.

## Final Implementation Order

1. Add public-seam regression coverage for supplemental jobs: enqueue, durable polling, failure detail, retry, and append-only writes.
2. Fix the shared task lifecycle and render phase/progress consistently in the UI.
3. Remove formal seed instances by explicit fixture IDs and prevent future automatic seeding; retain repository fixtures.
4. Introduce a derived location/entity coverage index without requiring manual entity maintenance.
5. Update reality-horror artifact generation so runtime-relevant locations receive independent, context-rich scene treatment.
6. Add fantasy/adventure classification for out-of-combat narrative, environment-led combat groups, adversary/support cards, and travel transitions.
7. Prototype and Playwright-test the coverage-to-supplemental-to-runtime flow before any broad visual redesign.
8. Reassess historical-version migration only after the new single-chapter acceptance gate passes.

## Stop Conditions

- Do not add new board types or rule-specific numeric conversions before the reality-horror chapter gate passes.
- Do not silently regenerate a full board from a supplemental request.
- Do not delete user-owned historical analysis versions automatically.
- Do not claim product completion from fixture or API-only tests; the real browser workflow must pass.
