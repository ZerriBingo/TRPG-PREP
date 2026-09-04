# Reassessment - 2026-08-31

Status: active implementation direction, reviewed 2026-09-03.

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

## Runtime

- Runtime is location-led and non-linear. A current location can lead play; triggers may be unhandled, active, or resolved without rewriting the approved artifact.
- Location granularity belongs to semantic whole-board planning and should be judged by investigation, interaction, help-seeking, revisit, clue, and event value.

## Runtime UI Principles

- The runtime page is the complete table-side reference surface, not a launcher back to the artifact page. It should make the chapter overview, approved locations, NPCs, threats, clocks, and display materials reachable during play.
- Location cards may lead navigation, while the arrangement of panels, filters, history, and supporting views remains an implementation choice tested against table-side retrieval.
- Persist state that affects play, such as clue visibility, trigger state, and clocks, separately from approved artifact content.
- A location is independent when it has investigation, interaction, help-seeking, revisit, clue, or event value; purely descriptive room lists remain inside their parent location.
- Candidate acceptance and bookshelf promotion remain explicit and auditable; the interface may combine them when the state transition stays clear.

## Implementation Order

1. Complete the location/overview artifact contract and location-led runtime flow.
2. Audit existing interfaces against current preparation and table-side workflows; simplify or remove surfaces that do not earn their complexity.
3. Finish display-material embedding in the artifact and runtime workflows.
4. Run offline contract tests and browser smoke tests without invoking a real long-running upstream task.
5. Ask the GM to run a fresh small reality-horror project, then the p96-p180 chapter, and report omissions and table-side retrieval friction.

## Skill Routing

Maintenance begins by checking installed skills. Use `diagnosing-bugs` for reported failures, `tdd` for public-contract changes, `codebase-design` and `domain-modeling` for interface changes, `writing-for-agents` for agent-facing documents, `playwright` for browser verification, and `code-review` for the final standards/spec pass. Use `grilling` only when a new product decision is genuinely unresolved.
