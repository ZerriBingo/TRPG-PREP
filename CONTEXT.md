# TRPG-PREP Context

## Purpose

TRPG-PREP turns uploaded module source into traceable GM preparation material. The user-facing product is organized around a bookshelf project, source analysis, candidate review, facts, artifact drafts, and runtime scenes.

## Current Priority

Stabilize the new-project chapter workflow before adding new product features. Every generated artifact must remain traceable to promoted fact IDs and must be reproducible from an explicit preparation scope. Historical runs and incidents are evidence for diagnosis, not requirements by themselves.

## Product Simplicity Principle

Every product surface should reduce preparation pressure or make table-side retrieval clearer. A new queue, repair mode, compatibility path, or control needs a current user need and an explicit design decision; an implementation accident is not a product requirement.

## Runtime Surface

Reality-horror runtime is a complete table-side reference surface: chapter overview, approved locations, NPCs, threats, clocks, and display materials remain reachable without returning to the artifact editor. Location cards can lead the experience because investigation, movement, and revisits are location-shaped, while layout, navigation, filters, and supporting views remain implementation choices guided by actual play. Runtime state persists separately from artifact content for clue visibility, triggers, clocks, and other play-state changes.

## Domain Terms

- **Workspace**: one GM preparation project and its saved analysis versions.
- **Source facts**: extracted, reviewed evidence tied to source pages.
- **Candidate**: a model-generated, source-bound proposal that still requires GM review.
- **Approved fact**: a candidate explicitly accepted as source material, inference, or GM-authored content.
- **Artifact**: a structured prep card derived from promoted facts.
- **Runtime plan**: a task-owned collection of approved location/environment cards. Its play state lives in a runtime snapshot and the plan may be deleted and rebuilt before play.
- **Display material**: a source-backed item a GM may show or hand to players (map, letter, photograph, newspaper, log, or similar). It keeps source pages and display notes; the tool does not crop or rewrite it.
- **Fact closure**: the complete set of promoted facts a card is allowed to cite. Every field citation must stay inside this set.
- **Runtime snapshot**: the state created when a scene starts; it can change during play without rewriting the artifact draft.
- **Semantic segment**: one logically coherent unit of source material selected for preparation. Its scope determines which facts belong together and where source ownership is anchored.
- **Transport window**: a request-sized reading view of one semantic segment. It may repeat nearby context or divide an oversized segment, but it is not an independent fact unit.
- **Window observation**: a model candidate produced while reading one transport window. It is internal evidence for later consolidation, not a user-facing review item.
- **Segment result**: the consolidated, source-bound candidate projection for one semantic segment. This is the reviewable output exposed to the GM.

## Engineering Principles

1. Do not expose concrete game-rule names in user-facing profile IDs, errors, or copyable keywords.
2. Keep each generation run coherent around its selected preparation scope. Retries and reruns preserve ownership and provenance, and do not silently combine unrelated or duplicate material.
3. Describe product behavior in terms of user goals and domain relationships before prescribing a particular panel, queue, filter, or navigation control.
4. Prefer small interfaces with deep implementations; keep storage, domain validation, and LLM adapters behind explicit seams.
5. Treat current user workflows as the design target. Compatibility, migration, and repair logic are deliberate product choices that must earn their complexity through a current user need.
6. Semantic segments own logical completeness and transport windows only control model request size. A transport split never becomes a new fact unit, and incomplete segment observations stay internal.
7. Task-owned intermediate analysis can be deleted with its preparation task; promoted source facts remain independent.

## Current Product Direction

- The first release gate is one complete chapter workflow, not whole-book automation.
- The bookshelf-to-runtime path is primary; facts remain source-backed material for review and retrieval.
- An artifact build represents one coherent result for one selected preparation task. It is reviewable, and a rerun must preserve clear ownership rather than silently append conflicting material.
- Important locations may form a non-linear network; runtime navigation must not force a route.
- Acceptance criteria are board-specific. The complete single-chapter runtime gate applies to the reality-horror board; other boards require their own confirmed criteria.
- A location is runtime-relevant when players may investigate it, return to it, seek help there, or use it to advance the fiction. Evidence-only locations may still need a compact independently visible scene treatment so the GM does not have to leave runtime mode to recover context.
- Long-running work exposes enough state for a user to understand progress, failure, recovery, and ownership.
- Fantasy/adventure preparation is split by play function: out-of-combat story material, in-combat scene material, and travel/transition material. A short adventure may need only a few strong beats; a city-based adventure may need a wider web of recurring places, factions, and social consequences.
- The fantasy board may borrow organizational principles from the supplied homebrew reference (clear campaign distinctions, inciting situation, GM-facing principles, locations, and concise adversary/environment support) while ignoring its system-specific numeric guidance and terminology in product output.
- In fantasy/adventure, an environment card can be the primary combat-scene operating surface, not merely an attachment. It should carry the scene's pressure, changing conditions, tactical/narrative prompts, and escalation cues; adversary cards are read alongside it and should stay concise.
- Fantasy artifact layout therefore follows function: a scene may have one environment card plus zero or more adversary/support cards, while out-of-combat and travel material remain separate. The generator must not produce a generic scene card that hides or duplicates the environment card's job.
- A fantasy combat scene is a grouped runtime unit: one environment-led operating card, optional adversary/support cards, and optional clocks or phase changes.
- City-scale fantasy preparation uses a hub-and-entity shape: a situation hub plus independently searchable recurring locations, people, factions, and consequences. One-off minor material may stay in a beat or hub.
- Travel and transition material is optional, supports detours, and never encodes a mandatory route.
- Source statistics are preserved only as cited source material. Any cross-board conversion is a GM-reviewable suggestion, never an automatic rewrite.
- Display materials use one shared model across boards, while board-specific recognition and presentation notes may differ. They are source-page records, not LLM-authored player text or crop uploads.
- Runtime is location-led and keeps non-linear exploration optional; it does not infer a mandatory route from page order.
- Reality-horror generates independent `location` cards for every named, visitable, returnable, investigable, or help-providing place. One-use triggers, display-material references, consequences, and revisit changes live inside the location card.
- Reality-horror generates a `chapter_overview` for cross-location background, truth, major threads, endings, and chapter-wide consequences. It does not generate an independent generic scene card.
- Display-material facts require explicit source evidence, and clock artifacts describe situation changes and progression without silently importing unrelated rule mechanics.

## Verification

Run focused Python checks plus JavaScript syntax checks before pushing. Real upstream experiments use an isolated database copy, and a release claim requires a fresh end-to-end user workflow rather than fixture-only or API-only evidence.
