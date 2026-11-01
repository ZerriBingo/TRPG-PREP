# Reassessment - 2026-08-31

Status: active implementation direction, reviewed 2026-09-03; fantasy/adventure direction recorded 2026-09-13.

The owner-confirmed product goals, preparation-time target, and ruleset boundaries are recorded in `PRODUCT_GUARDRAILS.md` under the sections dated 2026-10-31. Those decisions take precedence over conflicting earlier direction; implementation and release claims below remain historical evidence until revalidated. Current maintenance order and acceptance references are in `IMPLEMENTATION_BACKLOG.md`; the implementation order below describes the earlier board slices.

## Product Boundary

- The current workflow is designed around new projects. Historical projects and artifacts inform diagnosis and testing; migration or repair is a deliberate product choice that must serve a current user workflow.
- The three boards remain independent: reality-horror, fantasy/adventure, and general preparation.
- Semantic segmentation owns logical grouping. Transport and recovery strategies may vary, provided they preserve selected-source coverage, ownership, and provenance.

## Review And Facts

- Candidate review is organized around the current preparation task and current candidate records; its presentation should support scanning, source lookup, and efficient accept/reject/return actions.
- Accepted candidates enter the bookshelf through the normal review workflow; the user does not maintain semantic clusters or conflict groups.
- The facts page is a source-backed retrieval view. Additional relationship or navigation aids are justified only when they improve a real preparation workflow without replacing review and provenance.

## Reality-Horror Artifacts

- `location` is the main runtime card. Every named place players may visit, revisit, investigate, seek help at, or use fictionally receives an independent card, including small locations.
- A location card carries normal state, arrival description, relevant people, direct and hidden clues, GM responses, first-visit triggers, consequences, display-material references, and revisit changes.
- One-use events are embedded in their location card and tracked in the runtime snapshot. There is no independent generic scene card.
- `chapter_overview` carries cross-location situation, truth, major threads, endings, important people, cross-location clues, and chapter-wide consequences.
- NPC, threat, and clock cards remain available where their independent table-side value justifies them.
- Display materials retain source pages and may be referenced from location/overview cards. The application does not crop or save user screenshots.

## Fantasy/Adventure Artifacts

- Preparation is organized around narrative function rather than separate combat, travel, or world-map products. `environment` is the primary runtime surface: exploration, social interaction, traversal, combat, and events may all be led by one card, and a situation that moves between those modes does not become several duplicate cards.
- An environment card carries the scene's pressure, changing conditions, tactical and narrative prompts, and escalation cues. Adversary and clock cards are read alongside it as optional retrieval links and are never a preassembled encounter.
- `npc`, `enemy`, and `encounter_clock` stay available where their independent table-side value justifies them. Travel and transition material is optional, supports detours, and never encodes a mandatory route.
- Source statistics are preserved only as cited source material. Cross-board conversion is a GM-reviewable suggestion, never an automatic rewrite; the board does not generate replacement combat statistics or balance encounters automatically.
- Character-specific connections initially appear as questions or unresolved preparation prompts. A later party-context input may answer them without turning character details into source facts.
- Long-form preparation uses reviewed adventure chapters and cross-chapter story threads. Structure is proposed after fact review through bounded local observations and global consolidation, never from page cuts. Once the structure is approved, one artifact build covers every approved chapter: chapter-sized internal planning bounds each model request, while cross-chapter threads and fact dispositions stay visible to every scope.
- Beats are chapter-local combinations of applicable story threads. The product does not classify threads as main or sub, and does not ask the user to assemble chapters or beats by hand.

## Runtime

- Runtime is location-led and non-linear. A current location can lead play; triggers may be unhandled, active, or resolved without rewriting the approved artifact.
- Location granularity belongs to semantic whole-board planning and should be judged by investigation, interaction, help-seeking, revisit, clue, and event value.
- Reality-horror runtime is location-led and keeps non-linear exploration optional; fantasy/adventure runtime is environment-led. Neither infers a mandatory route from page order.

## Runtime UI Principles

- The runtime page is the complete table-side reference surface, not a launcher back to the artifact page. It should make the chapter overview, approved locations, NPCs, threats, clocks, and display materials reachable during play.
- Location cards may lead navigation, while the arrangement of panels, filters, history, and supporting views remains an implementation choice tested against table-side retrieval.
- Persist state that affects play, such as clue visibility, trigger state, and clocks, separately from approved artifact content.
- A location is independent when it has investigation, interaction, help-seeking, revisit, clue, or event value; purely descriptive room lists remain inside their parent location.
- Candidate acceptance and bookshelf promotion remain explicit and auditable; the interface may combine them when the state transition stays clear.

## Implementation Order

Reality-horror, satisfied by 0.1.4 and 0.1.5a0:

1. The location/overview artifact contract and the location-led runtime flow are implemented and released.
2. The interface audit continues as maintenance; surfaces that do not earn their complexity are removed rather than carried.
3. Display-material embedding is implemented in the artifact and runtime workflows.
4. Offline contract tests and browser smoke tests run without invoking a real long-running upstream task.
5. The GM ran fresh reality-horror projects and reported omissions and table-side retrieval friction; the single-chapter runtime gate is established.

Fantasy/adventure, current slice:

6. Review facts, then propose and confirm the adventure structure: chapters, cross-chapter story threads, chapter-local beats, and one explicit disposition for every promoted fact.
7. Generate every approved chapter in one artifact build, then review the result before anything reaches runtime.
8. Ask the GM to run the resulting short adventure at the table and report which prepared situations, motives, and pressures were usable.

## Skill Routing

Maintenance begins by checking installed skills. Use `diagnosing-bugs` for reported failures, `tdd` for public-contract changes, `codebase-design` and `domain-modeling` for interface changes, `writing-for-agents` for agent-facing documents, `playwright` for browser verification, and `code-review` for the final standards/spec pass. Use `grilling` only when a new product decision is genuinely unresolved.
