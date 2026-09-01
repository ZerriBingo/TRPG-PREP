# TRPG-PREP Context

## Purpose

TRPG-PREP turns uploaded module source into traceable GM preparation material. The user-facing product is organized around a bookshelf project, source analysis, candidate review, facts, artifact drafts, and runtime scenes.

## Current Priority

Stabilize the new-project chapter workflow before adding new product features. Every generated artifact must remain traceable to promoted fact IDs and must be recoverable by creating a new explicit-scope preparation task. The former incremental-card workflow is deleted from the product.

## Product Simplicity Principle

Remove any board, panel, or control that the GM cannot use in a clear preparation or table-side workflow. The product must reduce preparation pressure; it must not become a more complicated notebook than direct notes.

## Runtime Surface

Reality-horror runtime is a complete table-side reference surface: chapter overview, all approved locations, NPCs, threats, clocks, and display materials remain available without returning to the artifact page. The current location is the primary surface, with search and type filtering for the rest. There is no visible current-board panel or runtime log panel. Runtime state still persists for clue visibility, trigger state, and clock state.

## Domain Terms

- **Workspace**: one GM preparation project and its saved analysis versions.
- **Source facts**: extracted, reviewed evidence tied to source pages.
- **Artifact**: a structured prep card derived from promoted facts.
- **Runtime plan**: a task-owned collection of approved location/environment cards. Its play state lives in a runtime snapshot and the plan may be deleted and rebuilt before play.
- **Display material**: a source-backed item a GM may show or hand to players (map, letter, photograph, newspaper, log, or similar). It keeps source pages and display notes; the tool does not crop or rewrite it.
- **Fact closure**: the complete set of promoted facts a card is allowed to cite. Every field citation must stay inside this set.
- **Runtime snapshot**: the state created when a scene starts; it can change during play without rewriting the artifact draft.

## Engineering Rules

1. Do not expose concrete game-rule names in user-facing profile IDs, errors, or copyable keywords.
2. Full artifact generation for the selected preparation task is the only artifact-generation workflow; corrections require a new explicit-scope preparation task rather than an incremental repair job.
3. Artifact generation exposes one current queue per workspace and board. It may show planned-card
   progress, but never a user-managed history of parallel failed queues. Every planned card is attempted;
   after the queue finishes, the primary generation button becomes one batch "retry failed items" action.
   Successful internal steps are reused and no per-card retry controls are exposed.
4. Mechanical pagination is diagnostic only; source ranges may be discontinuous and cross page boundaries.
5. Prefer small interfaces with deep implementations; keep storage, domain validation, and LLM adapters behind explicit seams.
6. New project data is authoritative. Historical projects, links, jobs, and artifacts are not migrated, rewritten, or given compatibility-only repair controls.
7. When review first creates a bookshelf, the client must adopt the returned workspace ID before any shelf navigation. A successful promotion must never require a hard browser refresh to reveal the new facts.

## Resolved Product Direction (2026-08-29)

- The first release gate is one complete chapter workflow, not whole-book automation.
- The bookshelf-to-runtime path is primary; the facts page is a source-backed search view, not a graph editor or relationship-maintenance surface.
- Artifact generation is a single task-owned board build. It is reviewable and never a silent append or partial regeneration.
- Important locations may form a non-linear network; runtime navigation must not force a route.
- Acceptance criteria are board-specific. The complete single-chapter runtime gate applies to the reality-horror board; other boards require their own confirmed criteria.
- A location is runtime-relevant when players may investigate it, return to it, seek help there, or use it to advance the fiction. Evidence-only locations may still need a compact independently visible scene treatment so the GM does not have to leave runtime mode to recover context.
- Job state is a shared product concept across analysis, review, and full artifact generation; each job exposes phase, progress, failure, and retry semantics.
- Fantasy/adventure preparation is split by play function: out-of-combat story material, in-combat scene material, and travel/transition material. A short adventure may need only a few strong beats; a city-based adventure may need a wider web of recurring places, factions, and social consequences.
- The fantasy board may borrow organizational principles from the supplied homebrew reference (clear campaign distinctions, inciting situation, GM-facing principles, locations, and concise adversary/environment support) while ignoring its system-specific numeric guidance and terminology in product output.
- Seed projects are fixtures for development only. They must not appear in or remain undeletable in a user's formal bookshelf. Fixture JSON may remain in the repository for tests.
- In fantasy/adventure, an environment card can be the primary combat-scene operating surface, not merely an attachment. It should carry the scene's pressure, changing conditions, tactical/narrative prompts, and escalation cues; adversary cards are read alongside it and should stay concise.
- Fantasy artifact layout therefore follows function: a scene may have one environment card plus zero or more adversary/support cards, while out-of-combat and travel material remain separate. The generator must not produce a generic scene card that hides or duplicates the environment card's job.
- A fantasy combat scene is a grouped runtime unit: one environment-led operating card, optional adversary/support cards, and optional clocks or phase changes.
- City-scale fantasy preparation uses a hub-and-entity shape: a situation hub plus independently searchable recurring locations, people, factions, and consequences. One-off minor material may stay in a beat or hub.
- Travel and transition material is optional, supports detours, and never encodes a mandatory route.
- Source statistics are preserved only as cited source material. Any cross-board conversion is a GM-reviewable suggestion, never an automatic rewrite.
- Seed cleanup is an explicit fixture-ID operation against formal user data. Repository fixtures remain for tests; user-owned historical analysis versions are not touched by seed cleanup.
- The task center is a shared user-facing surface with job type, phase, progress, failure, retry, and replace impact.
- Display materials use one shared model across boards, while board-specific recognition and presentation notes may differ. They are source-page records, not LLM-authored player text or crop uploads.
- Runtime is location-led and keeps non-linear exploration optional; it does not infer a mandatory route from page order.
- The old incremental-card/"补卡" entry point is deleted. New projects show uncovered material as a warning; correcting the scope means creating or re-running an explicit preparation task, never accumulating repair jobs.
- Location facts and local digest labels are not treated as a deterministic missing-card checklist. Location granularity is decided semantically during whole-board planning; the bookshelf exposes no parallel source-check or exclusion workflow.
- Candidate review is current-record editing: edits replace the candidate and return it to review. Split/merge replace the selected candidate set, preserve only the submitted result, and never retain old model content in review history.
- Review history is operation metadata only (action, explanation, source/field labels, related IDs, and time). Promotion copies the current candidate into an independent fact; later candidate edits do not change that fact.
- Candidate review is a flat, paginated task queue with source-page filtering and bulk actions. The retired clustering/conflict workbench does not exist in the current product.
- Reality-horror generates independent `location` cards for every named, visitable, returnable, investigable, or help-providing place. One-use triggers, display-material references, consequences, and revisit changes live inside the location card.
- Reality-horror generates a `chapter_overview` for cross-location background, truth, major threads, endings, and chapter-wide consequences. It does not generate an independent generic scene card.
- Runtime refreshes the current bookshelf after review mutations and when entering the shelf view; runtime locations remain visible regardless of the adjacent reference filter, while that filter controls only non-location references.
- Runtime session status uses explicit wording (`运行状态待保存` / `运行状态已保存`) so it is not confused with artifact editing.
- Location card fields such as relevant characters, hidden clues, return changes, and direct clues are optional but the materializer must actively inspect for supported content. `gm_moves` may be a clearly marked GM-authored operating prompt and is not presented as source fact.
- Display-material recognition prefers strong text labels and PDF layout metadata. Formal materials keep source-page records and optional image-region metadata; no crop upload or in-app crop editor is created. Unlabelled images remain internal visual candidates and never block preparation.
- Batch candidate review must promote prep-owned candidates in the same request. The task-owned endpoint is preferred, while the generic shadow batch endpoint also detects prep ownership as a defensive fallback; no path may leave a candidate merely marked accepted without a bookshelf fact. When a review page is still loading, post-mutation refresh waits for that request to finish instead of silently skipping the refresh.
- Display materials are runtime context, not generic reference cards: a location card may surface materials whose source facts belong to that location, while unassociated materials remain in the dedicated unassociated list. Location navigation has no separate empty beat panel, and review history is internal audit metadata rather than a user-facing workbench section.
- Formal display-material facts are created only from explicit source labels; generic PDF text blocks and unlabeled image captions never become handouts or facts. Shadow analysis tasks whose idempotency key names a deleted prep job are orphan records and must be removed from task listings and storage cleanup.
- Display-material recognition is no longer a formal preparation pipeline. Runtime shows a compact source-page hint box; locations do not absorb or link handout cards, and confirmed display materials are not placed in generic runtime reference cards. Clock artifacts may be narratively transformed for modern play; their stages describe situation changes and progression, not attack-count sequences, and they do not require an old module to have used a clock explicitly.

## Verification

Run focused Python checks plus `node --check frontend/workbench.js` before pushing. Real upstream experiments must use an isolated database copy.
