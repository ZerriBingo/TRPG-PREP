# Implementation Backlog

Status: active after the Q1-Q81 reassessment on 2026-08-31.

The authoritative product contract is `REASSESSMENT_2026-08-31.md`. New-project behavior is authoritative; old projects and artifacts are not migration targets.

## Completed

- PDF upload, deletion when unreferenced, and uploaded-source discovery on page load.
- Explicit continuous or discontinuous page ranges.
- Semantic segmentation as the primary path, with mechanical windows only as a visible fallback after semantic failure.
- Durable LLM jobs with phase, progress, retry, stored attempts, and atomic artifact writes.
- Flat candidate review with task/page filters, pagination, batch actions, current-record editing, split/merge replacement, and explicit promotion to bookshelf facts.
- Three independent user-facing boards: reality horror, fantasy/adventure, and general preparation.
- Search-only facts page with keyword, kind, visibility, and source-page filters.
- Full-board artifact generation only; the incremental repair-card workflow is removed.
- Reality-horror artifact contract: independent `location` cards, one `chapter_overview`, and optional NPC, threat, clock, and display-material records.
- Location-led reality-horror runtime: free location switching, trigger states, clue state, clocks, GM notes, and location-bound display materials.
- Parallel source checks, location coverage, and exclusion controls are retired; artifact review and runtime are the only product surfaces for generated material.

## Next Engineering Work

1. Browser smoke-test the full new-project UI without invoking a real long-running model job.
2. Remove remaining stale scene/beat wording where it leaks into the reality-horror path; retain beat navigation only for boards whose runtime contract still uses it.
3. Add focused HTTP regression coverage for location-bound display-material updates.
4. Review fantasy/adventure generation against environment-led combat units, out-of-combat hubs, and optional travel material.
5. Define and implement the independent general-preparation artifact contract without turning it into a conversion stage for the other boards.
6. Run a two-axis standards/spec review from baseline `b83e380` and resolve findings.

## Human Validation Gates

After offline and browser checks pass:

1. Create a fresh small reality-horror project and verify every visitable or returnable place is independently available at runtime.
2. Confirm location triggers, clue state, display-material references, and return changes are usable without returning to the facts page.
3. Create a fresh p96-p180 chapter project and report omitted locations, duplicate cards, source-page lookups, manual edits, and table-side retrieval friction.

Human validation evaluates the new project only. It does not require repairing historical artifacts.

## Retired Directions

- Candidate clustering and conflict workbench.
- Relationship graph, LLM index, manual relationship maintenance, and page-fact preparation.
- Predicted session duration.
- Generic reality-horror scene cards and linear beat navigation.
- Incremental repair or supplemental card jobs.
- Seed projects in the formal bookshelf.
