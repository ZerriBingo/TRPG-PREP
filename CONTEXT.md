# TRPG-PREP Context

## Purpose

TRPG-PREP turns uploaded module source into traceable GM preparation material. The user-facing product is organized around a bookshelf project, source analysis, candidate review, facts, artifact drafts, and runtime scenes.

## Current Priority

Choose maintenance work and acceptance criteria from `docs/IMPLEMENTATION_BACKLOG.md`, following the owner-confirmed goals and ruleset boundaries in `docs/PRODUCT_GUARDRAILS.md` (2026-10-31). Every generated artifact must remain traceable to promoted fact IDs and reproducible from an explicit preparation scope. Historical runs and incidents are evidence for diagnosis, not requirements by themselves.

## Product Simplicity Principle

Every product surface should reduce preparation pressure or make table-side retrieval clearer. A new queue, repair mode, compatibility path, or control needs a current user need and an explicit design decision; an implementation accident is not a product requirement.

## Runtime Surface

Reality-horror runtime is a complete table-side reference surface: chapter overview, approved locations, NPCs, threats, clocks, and display materials remain reachable without returning to the artifact editor. Location cards can lead the experience because investigation, movement, and revisits are location-shaped, while layout, navigation, filters, and supporting views remain implementation choices guided by actual play. Runtime state persists separately from artifact content for clue visibility, triggers, clocks, and other play-state changes.

## Domain Terms

- **Workspace**: one GM preparation project and its saved analysis versions.
- **Preparation board**: a preparation style with its own information needs, such as reality-horror or fantasy/adventure; it does not prescribe the rules used at the table.
- **Play ruleset**: the rules the GM uses to run the game; it is distinct from the rules under which a source module was written.
- **Design reference**: material whose preparation or game-design methods inform the product, such as Cthulhu Dark or Daggerheart; it does not determine the play ruleset.
- **Source facts**: extracted, reviewed evidence tied to source pages.
- **Candidate**: a model-generated, source-bound proposal that still requires GM review.
- **Approved fact**: a candidate explicitly accepted as source material, inference, or GM-authored content.
- **Artifact**: a structured prep card derived from promoted facts.
- **Runtime plan**: a task-owned collection of approved location/environment cards. Its play state lives in a runtime snapshot and the plan may be deleted and rebuilt before play.
- **Display material**: a source-backed item a GM may show or hand to players (map, letter, photograph, newspaper, log, or similar). It keeps source pages and display notes; the tool does not crop or rewrite it.
- **Fact closure**: the complete set of promoted facts a card is allowed to cite. Every field citation must stay inside this set.
- **Runtime snapshot**: the state created when a scene starts; it can change during play without rewriting the artifact draft.
- **Fantasy environment**: a runnable situation shaped by a place, activity, background characters, and active forces. It may support exploration, social conflict, traversal, combat, or an event; it is not synonymous with a battle map.
- **Fantasy runtime relation**: an optional retrieval link between an environment and relevant NPC, adversary, or countdown support. It never requires those elements to appear together or fixes encounter order.
- **Party context**: optional GM-authored information used to connect prepared situations to the current characters. It remains separate from source facts and never changes source provenance.
- **Semantic segment**: one logically coherent unit of source material selected for preparation. Its scope determines which facts belong together and where source ownership is anchored.
- **Transport window**: a request-sized reading view of one semantic segment. It may repeat nearby context or divide an oversized segment, but it is not an independent fact unit.
- **Window observation**: a model candidate produced while reading one transport window. It is internal evidence for later consolidation, not a user-facing review item.
- **Segment result**: the consolidated, source-bound candidate projection for one semantic segment. This is the reviewable output exposed to the GM.
- **Adventure chapter**: a reviewed narrative preparation scope containing possible beats and applicable facts. It is not a page range, source partition, session count, or mandatory runtime route.
- **Story beat**: a proposed narrative focus for a possible game within one chapter. It explains what each selected story thread may change in that game, what remains open afterward, and how those developments interact. It is not an environment or a list of scene visits, and never requires a fixed runtime order or game count. Story threads, rather than beats, may cross chapters.
- **Story thread**: a reviewed storyline such as a faction, truth chain, objective, or character fate. It can be introduced, developed, resolved, or remain background in several chapters; these roles are organizational suggestions rather than source facts.
- **Fact disposition**: the explicit reviewed use of one promoted fact: applicable to one or more chapters, retained as global reference, recognized as display material, or deliberately deferred with a reason. It prevents structure planning from silently dropping material.

## Engineering Principles

1. Do not expose concrete game-rule names in user-facing profile IDs, errors, or copyable keywords.
2. Keep each generation run coherent around its selected preparation scope. Retries and reruns preserve ownership and provenance, and do not silently combine unrelated or duplicate material.
3. Describe product behavior in terms of user goals and domain relationships before prescribing a particular panel, queue, filter, or navigation control.
4. Prefer small interfaces with deep implementations; keep storage, domain validation, and LLM adapters behind explicit seams.
5. Treat current user workflows as the design target. Compatibility, migration, and repair logic are deliberate product choices that must earn their complexity through a current user need.
6. Semantic segments own logical completeness and transport windows only control model request size. A transport split never becomes a new fact unit, and incomplete segment observations stay internal.
7. Task-owned intermediate analysis can be deleted with its preparation task; promoted source facts remain independent.

## Current Product Direction

- Each board earns completion through its own end-to-end vertical slice rather than whole-book automation. The current fantasy/adventure slice targets one complete short adventure.
- The bookshelf-to-runtime path is primary; facts remain source-backed material for review and retrieval.
- An artifact build represents one coherent result for one selected preparation task. It is reviewable, and a rerun must preserve clear ownership rather than silently append conflicting material.
- Important locations may form a non-linear network; runtime navigation must not force a route.
- Acceptance criteria are board-specific. The complete single-chapter runtime gate applies to the reality-horror board; other boards require their own confirmed criteria.
- A location is runtime-relevant when players may investigate it, return to it, seek help there, or use it to advance the fiction. Evidence-only locations may still need a compact independently visible scene treatment so the GM does not have to leave runtime mode to recover context.
- Long-running work exposes enough state for a user to understand progress, failure, recovery, and ownership.
- Fantasy/adventure preparation is organized around narrative function rather than separate combat, travel, or world-map products. A short adventure may need only a few strong beats; a long adventure may need reviewed chapters and cross-chapter threads.
- The fantasy board may borrow organizational principles from the supplied homebrew reference (clear campaign distinctions, inciting situation, GM-facing principles, locations, and concise adversary/environment support) while ignoring its system-specific numeric guidance and terminology in product output.
- In fantasy/adventure, an environment card can be the primary combat-scene operating surface, not merely an attachment. It should carry the scene's pressure, changing conditions, tactical/narrative prompts, and escalation cues; adversary cards are read alongside it and should stay concise.
- Fantasy artifact layout follows narrative function. An environment may support exploration, social interaction, traversal, combat, or an event without becoming separate cards for each mode. Related adversaries and clocks are optional retrieval links, not a preassembled encounter.
- Travel and transition material is optional, supports detours, and never encodes a mandatory route.
- Source statistics are preserved only as cited source material. Any cross-board conversion is a GM-reviewable suggestion, never an automatic rewrite.
- The first fantasy/adventure slice organizes narrative function, motives, environment pressure, responses, phase changes, and nonviolent outcomes. It does not generate replacement combat statistics or attempt to balance encounters automatically.
- Character-specific connections initially appear as questions or unresolved preparation prompts. A later party-context input may answer them without turning character details into source facts.
- Display materials use one shared model across boards, while board-specific recognition and presentation notes may differ. They are source-page records, not LLM-authored player text or crop uploads.
- Reality-horror runtime is location-led and keeps non-linear exploration optional; fantasy/adventure runtime is environment-led. Neither infers a mandatory route from page order.
- Reality-horror generates independent `location` cards for every named, visitable, returnable, investigable, or help-providing place. One-use triggers, display-material references, consequences, and revisit changes live inside the location card.
- Reality-horror generates a `chapter_overview` for cross-location background, truth, major threads, endings, and chapter-wide consequences. It does not generate an independent generic scene card.
- Display-material facts require explicit source evidence, and clock artifacts describe situation changes and progression without silently importing unrelated rule mechanics.
- Long-form preparation may be organized on two reviewed axes: adventure chapters provide narrative preparation scopes and story threads describe cross-chapter applicability. Facts may support several chapters or remain global reference material. Structure is proposed after fact review through bounded local observations and global consolidation; it is not added to semantic extraction and is never inferred from page cuts alone.
- Beats are chapter-local combinations of applicable story threads. The product does not classify threads as main or sub, and does not ask the user to manually assemble chapters or beats before generation.
- Once the fantasy/adventure structure is approved, one artifact build covers every approved chapter. Chapter-sized internal planning keeps model requests bounded, while cross-chapter threads and fact dispositions remain visible to each scope; chapter selection is a review and retrieval concern, not a way to omit chapters from generation.

## Verification

Run focused Python checks plus JavaScript syntax checks before pushing. Real upstream experiments use an isolated database copy, and a release claim requires a fresh end-to-end user workflow rather than fixture-only or API-only evidence. A stage reached only through injected fixtures or hand-written API calls is component-complete, never product-complete.
