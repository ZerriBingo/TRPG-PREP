# TRPG-PREP Context

## Purpose

TRPG-PREP turns uploaded module source into traceable GM preparation material. The user-facing product is organized around a bookshelf project, source analysis, candidate review, facts, artifact drafts, and runtime scenes.

## Current Priority

Stabilize engineering governance and the supplemental location-card workflow before adding new product features. Every generated artifact must remain traceable to promoted fact IDs and must be incrementally recoverable after failure.

## Domain Terms

- **Workspace**: one GM preparation project and its saved analysis versions.
- **Source facts**: extracted, reviewed evidence tied to source pages.
- **Artifact**: a structured prep card derived from promoted facts.
- **Runtime scene**: an assembled, locked-at-runtime plan that may be deleted and rebuilt before play.
- **Supplemental card job**: an incremental artifact job scoped to uncovered facts; it may append to an existing artifact set.
- **Fact closure**: the complete set of promoted facts a card is allowed to cite. Every field citation must stay inside this set.
- **Coverage audit**: a report over source evidence that distinguishes deterministic card coverage from locations needing GM review.
- **Runtime snapshot**: the state created when a scene starts; it can change during play without rewriting the artifact draft.

## Engineering Rules

1. Do not expose concrete game-rule names in user-facing profile IDs, errors, or copyable keywords.
2. Full artifact generation and supplemental generation are separate workflows with separate duplicate semantics.
3. Background jobs must expose durable status, phase, progress, and failure details to the UI.
4. Mechanical pagination is diagnostic only; source ranges may be discontinuous and cross page boundaries.
5. Prefer small interfaces with deep implementations; keep storage, domain validation, and LLM adapters behind explicit seams.

## Resolved Product Direction (2026-08-29)

- The first release gate is one complete chapter workflow, not whole-book automation.
- The bookshelf-to-runtime path is primary; the facts graph is a secondary retrieval and provenance view.
- Supplemental cards are incremental and reviewable, never a silent full regeneration.
- Important locations may form a non-linear network; runtime navigation must not force a route.
- Acceptance criteria are board-specific. The complete single-chapter runtime gate applies to the reality-horror board; other boards require their own confirmed criteria.
- A location is runtime-relevant when players may investigate it, return to it, seek help there, or use it to advance the fiction. Evidence-only locations may still need a compact independently visible scene treatment so the GM does not have to leave runtime mode to recover context.
- Job state is a shared product concept across analysis, review, artifact generation, and supplemental work; each job exposes phase, progress, failure, and retry semantics.

## Verification

Run focused Python checks plus `node --check frontend/workbench.js` before pushing. Real upstream experiments must use an isolated database copy.
