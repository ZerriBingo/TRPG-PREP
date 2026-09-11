# Implementation Backlog

Status: fantasy/adventure vertical slice active; reviewed 2026-09-06.

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
- The `0.1.4` user package and reality-horror release gate were completed without bundling local sources, generated artifacts, or development fixtures.

## Fantasy Slice Implemented

1. The profile and validation contract now define broad environment situations, NPC and enemy support, explicit encounter groups, encounter clocks, and package-level runtime brief metadata. Meaningful traversal is represented by an environment with `environment_type=traversal`, not a separate travel card.
2. Local classification and whole-board planning now preserve situation function, motives, nonviolent outcomes, phase changes, source ownership, and the boundary against generated combat statistics.
3. Artifact generation, review, scene assembly, and runtime navigation support environment-led play while reusing shared provenance, clock, persistence, and source-lookup infrastructure.
4. Offline domain, HTTP, prompt, reality-horror regression, and desktop/mobile browser checks pass.

## Next Engineering Work

1. Add a post-review structure job that proposes local story beats and thread mentions in bounded batches, then consolidates only their compact indexes.
2. Add review controls for adventure chapters, beats, and cross-chapter thread roles before any chapter-specific artifact generation is enabled.
3. Generate every approved chapter and its applicable artifacts as one coherent build. Every promoted fact receives an explicit reviewed disposition, while only facts with a card-worthy narrative function need become cards.
4. Validate short material as a one-chapter case and long material with cross-chapter facts, global references, and recurring threads.
5. Define and implement the independent general-preparation artifact contract without turning it into a conversion stage for the other boards.

## Regression Baseline

Reality-horror remains a supported released board. Changes to shared task, provenance, artifact, clock, source-lookup, or runtime infrastructure must preserve its location-led workflow through focused regression tests. Historical projects and artifacts are diagnostic evidence, not release requirements unless compatibility is explicitly included in scope.

## Fantasy/Adventure Vertical Slice Gate

The first fantasy/adventure gate uses `狂野逐羊` as one complete short adventure. It passes only when:

1. Every major playable situation is independently reachable in artifact review and runtime.
2. Each battle has a source-backed narrative function and at least one objective beyond defeating every opponent.
3. Important NPCs and adversaries have usable motives; hostile situations preserve retreat, surrender, negotiation, bypass, or another supported outcome where the source allows it.
4. Environments expose usable features, dangers, GM responses, phase changes, and exits without becoming longer generic summaries.
5. Ordinary travel is skipped while meaningful travel choices remain optional and non-linear.
6. Every generated statement remains traceable to promoted facts or is explicitly marked as a GM suggestion or unresolved question.
7. The main situations can be run without returning to the facts page.

The short-adventure gate remains a regression baseline. Long-form work must use the same narrative artifact contracts while adding reviewed chapters, possible beats, and cross-chapter threads; it must not add page-shaped preparation units or a world-map product.
