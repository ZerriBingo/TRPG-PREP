# Reassessment - 2026-08-31

Status: active implementation direction after Q1-Q81.

## Product Boundary

- New-project behavior is authoritative. Historical projects and artifacts are not migrated or given compatibility-only repair controls.
- The three boards remain independent: reality-horror, fantasy/adventure, and general preparation.
- Preparation tasks do not ask for a predicted session duration.
- Semantic segmentation is the primary path. Pending jobs show only preparation state; a validated semantic plan owns the selected pages, while mechanical windows are a visible fallback after semantic failure.

## Review And Facts

- Candidate review is a flat, paginated task queue with source-page filtering and bulk accept/reject/return actions.
- Accepted candidates enter the bookshelf through the normal review workflow; the user does not maintain semantic clusters or conflict groups.
- Candidate clustering, conflict workbench routes, and their persistence model are retired.
- The facts page is search-only: keyword, type, visibility, and source-page filters plus full source-backed fact details.
- Relationship graphs, LLM indexes, manual relationship maintenance, “prepare facts by page,” and fact creation from that page are retired.

## Reality-Horror Artifacts

- `location` is the main runtime card. Every named place players may visit, revisit, investigate, seek help at, or use fictionally receives an independent card, including small locations.
- A location card carries normal state, arrival description, relevant people, direct and hidden clues, GM responses, first-visit triggers, consequences, display-material references, and revisit changes.
- One-use events are embedded in their location card and tracked in the runtime snapshot. There is no independent generic scene card.
- `chapter_overview` carries cross-location situation, truth, major threads, endings, important people, cross-location clues, and chapter-wide consequences.
- NPC, threat, and clock cards remain available where their independent table-side value justifies them.
- Display materials retain source pages and may be referenced from location/overview cards. The application does not crop or save user screenshots.

## Runtime

- Runtime is location-led and non-linear. The current location is the main surface; triggers may be unhandled, active, or resolved without rewriting the approved artifact.
- Location granularity belongs to semantic whole-board planning. The bookshelf has no parallel source-check, missing-location warning, or location-exclusion workflow.

## Runtime UI Principles

- The runtime page is the complete table-side reference surface, not a launcher back to the artifact page.
- Reality-horror plans expose the chapter overview, every approved location, NPCs, threats, clocks, and display materials from the runtime page.
- The current location is the main surface; all other runtime material is reachable through search, type filters, and a compact related-material area.
- Remove the visible current-board panel and runtime log panel. Persist only state that affects play, such as clue visibility, trigger state, and clocks.
- Remove previous/next location controls and any route inferred from fact relationships.
- A location is independent when it has investigation, interaction, help-seeking, revisit, clue, or event value; purely descriptive room lists remain inside their parent location.
- Candidate acceptance and bookshelf promotion are one user-facing action, even if the backend retains resumable internal stages.

## Implementation Order

1. Complete the location/overview artifact contract and location-led runtime flow.
2. Remove retired cluster, graph, page-fact, duration, generic-scene, and beat-navigation interfaces end to end.
3. Finish display-material embedding in the artifact and runtime workflows.
4. Run offline contract tests and browser smoke tests without invoking a real long-running upstream task.
5. Ask the GM to run a fresh small reality-horror project, then the p96-p180 chapter, and report omissions and table-side retrieval friction.

## Skill Routing

Maintenance begins by checking installed skills. Use `diagnosing-bugs` for reported failures, `tdd` for public-contract changes, `codebase-design` and `domain-modeling` for interface changes, `writing-for-agents` for agent-facing documents, `playwright` for browser verification, and `code-review` for the final standards/spec pass. Use `grilling` only when a new product decision is genuinely unresolved.
