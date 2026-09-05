# Implementation Backlog

Status: active after the Q1-Q81 reassessment; reviewed 2026-09-04.

The current product direction is `REASSESSMENT_2026-08-31.md`. New projects are the primary validation target; historical projects and artifacts inform diagnosis, while compatibility or migration requires an explicit user-facing rationale.

## Completed

- PDF upload, deletion when unreferenced, and uploaded-source discovery on page load.
- Explicit continuous or discontinuous page ranges.
- Semantic segmentation as the primary logical grouping; transport and recovery details remain implementation concerns as long as source coverage and ownership are preserved.
- Durable LLM jobs with phase, progress, retry, stored attempts, and atomic artifact writes.
- Candidate review with task context, source lookup, efficient bulk actions, current-record editing, split/merge replacement, and explicit promotion to bookshelf facts.
- Three independent user-facing boards: reality horror, fantasy/adventure, and general preparation.
- Search-only facts page with keyword, kind, visibility, and source-page filters.
- Coherent full-board artifact generation for the selected preparation task, with explicit ownership across retries and reruns.
- Reality-horror artifact contract: independent `location` cards, one `chapter_overview`, and optional NPC, threat, clock, and display-material records.
- Location-led reality-horror runtime: free location switching, trigger states, clue state, clocks, GM notes, and location-bound display materials.
- Artifact review and runtime are the primary product surfaces for generated material; any additional surface must earn its place through a current workflow.

## Next Engineering Work

1. Complete the real-upstream semantic-analysis and artifact smoke gate for the `0.1.4a0` changes recorded in `MAINTENANCE_2026-09-04_RELEASE_0.1.3.md`.
2. Browser smoke-test the full new-project UI without invoking a real long-running model job.
3. Review reality-horror runtime wording and controls against location-led play; keep the interaction model flexible while preserving non-linear retrieval.
4. Add focused HTTP regression coverage for location-bound display-material updates.
5. Review fantasy/adventure generation against environment-led combat units, out-of-combat hubs, and optional travel material.
6. Define and implement the independent general-preparation artifact contract without turning it into a conversion stage for the other boards.
7. Run a two-axis standards/spec review from baseline `b83e380` and resolve findings.

## Human Validation Gates

After offline and browser checks pass:

1. Create a fresh small reality-horror project and verify every visitable or returnable place is independently available at runtime.
2. Confirm location triggers, clue state, display-material references, and return changes are usable without returning to the facts page.
3. Create a fresh p96-p180 chapter project and report omitted locations, duplicate cards, source-page lookups, manual edits, and table-side retrieval friction.

Fresh projects are the primary release validation target. Historical artifacts are not a release requirement unless compatibility is explicitly included in the release scope.
