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
- The task center is a shared user-facing surface with job type, phase, progress, failure, retry, and append/replace impact.

## Verification

Run focused Python checks plus `node --check frontend/workbench.js` before pushing. Real upstream experiments must use an isolated database copy.
