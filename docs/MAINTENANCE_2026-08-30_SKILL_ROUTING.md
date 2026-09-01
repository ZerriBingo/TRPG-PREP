# Maintenance: skill routing checkpoint

Date: 2026-08-30
Status: active implementation guidance

## Why this record exists

The previous maintenance turn spent too long in implementation before checking
the installed skills. This record makes the routing decision explicit before
another code change. The repository uses the installed skills as task-intent
guidance; it does not copy third-party skill directories into the project.

## Inventory and routing

- `setup-matt-pocock-skills`: already completed. `docs/agents/issue-tracker.md`
  and the domain documentation layout are present; do not repeat setup during
  ordinary maintenance.
- `diagnosing-bugs`: use when a concrete failure, exception, timeout, or
  regression is reported. Establish a tight red-capable repro before changing
  implementation. The current review-queue and graph work is an enhancement
  slice, so this skill is only used for any failure found while verifying it.
- `tdd`: use at the public domain seam for each new behavior. The graph builder
  and candidate source-edit contract therefore get focused regression tests
  before their implementation is expanded.
- `codebase-design`: use for the graph and review interfaces. Keep the graph
  behind a small domain interface and keep storage/HTTP adapters outside it.
- `domain-modeling`: use when adding the relation-graph vocabulary or changing
  the meaning of candidate provenance; update `CONTEXT.md` in the same change.
- `writing-for-agents`: use when changing this routing note or any document
  consumed by future agents. Keep the routing pointer short and disclose
  detailed guidance here.
- `playwright`: use after the public contracts pass to verify the actual
  desktop/mobile workbench flow, especially task labels, graph focus/expand,
  and structured source-reference editing.
- `code-review`: run from fixed commit `b83e380` after implementation and
  verification. Review standards and spec separately; stale findings from
  earlier diffs are not evidence against the current tree.

## Deliberately deferred

- `grilling`: the current product decisions were already confirmed by the
  latest accepted recommendation round; invoke it again only when verification
  exposes a new unresolved product choice.
- `research`: no external fact is required for these local contracts.
- `prototype` / UI mockup: the graph state model is now specified; a throwaway
  visual prototype would add delay rather than resolve an open decision.
- migration skills: no persisted schema migration is needed; graph data is
  derived and candidate edits reuse existing fields.
- generic orchestration, wayfinding, and ticket skills: they add process
  overhead for this bounded maintenance slice.

## Completion gate for this slice

The slice is complete only when the focused tests, `compileall`, JavaScript
syntax check, `git diff --check`, and a real browser smoke check all pass, and
the implementation no longer exposes internal task IDs or legacy reviewed
content in user-facing review controls.

## Execution record (2026-08-30)

Skills were checked before this maintenance edit. The active routes were
`tdd` (public contracts), `codebase-design` (small review/graph interfaces),
`domain-modeling` (candidate and relationship vocabulary),
`writing-for-agents` (this record), `playwright` (browser smoke),
`diagnosing-bugs` (only for a concrete verification failure), and
`code-review` (fixed point `b83e380`). `grilling`, `research`, `prototype`,
and migration skills remained deferred because no new product decision,
external fact, visual prototype, or persisted schema migration was required.

The focused regression set passed for candidate editing, new-project scope,
handout coverage, relation graph, preparation retry/model switching, artifact
workflow, runtime review, and shadow review. `python -m compileall -q backend`,
`node --check frontend/workbench.js`, and `git diff --check` also passed.
Browser smoke covered the empty workspace, relation graph focus/expand,
structured source editing, desktop layout, and 390px layout. The older
`test_cancel.py`, `test_chunks.py`, `test_incremental.py`, `test_filter.py`,
and `test_staged.py` scripts depend on a PDF outside the repository or on the
retired legacy pipeline; their fixture failure is not a current-project
regression and must not be used as the release gate.

The review view now keeps candidate/task/fact IDs only in request paths and
`data-*` attributes. Visible labels use candidate ordinal, kind, source pages,
and fact text; replaced historical candidates use a neutral label.

## Execution record (2026-08-30, continued)

The installed skill library was checked again before this implementation slice.
The active routes are `tdd` for the four public behavior contracts,
`codebase-design` for task scope, relation confirmation, and runtime display
seams, `domain-modeling` for the confirmed-link vocabulary, `writing-for-agents`
for this maintenance record, `playwright` for the final workbench smoke, and
`code-review` from fixed point `b83e380`. `diagnosing-bugs` remains reserved for
a concrete verification failure; `grilling`, `research`, `prototype`, and
migration skills stay deferred because this slice has no unresolved product
choice, external fact, visual design experiment, or persisted schema migration.

The implementation target is deliberately limited to new-project behavior:
runtime-plan card selection must honor the current preparation task's source
and page spans; important location units must have independent runtime anchor
cards; candidate `possible_links` must become fact links only through explicit
promotion confirmation; and runtime must retain a global list of confirmed
display materials that are not associated with a beat. No compatibility or
incremental repair path is added for historical artifacts.

## Execution record (2026-08-30, verification closeout)

The focused regression set initially exposed a stale fixture in
`test_scene_plan_context.py`: it paired a p2-4 preparation task with seed facts
referencing p159-165. The fixture was corrected to use the task-owned source
and page, preserving the scope rejection contract. Promotion idempotency was
also tightened: repeating the same evidence status and confirmed-link set is
an idempotent success; changing either is a conflict. Batch promotion remains
relationship-free by design, so per-candidate relationship checkboxes cannot
leak into another candidate.

Verification completed with all focused Python and JavaScript checks passing,
`python -m compileall -q backend`, both JavaScript syntax checks, and
`git diff --check`. Playwright smoke ran against the live service on port
8002 at desktop and 390px widths; the empty workbench, task list, neutral
board labels, cross-page windows, review queue, and disabled empty-state
controls loaded without browser errors. Port 8000 was already occupied by the
user's running service and was left untouched.

The fixed-point review route is active from `b83e380`; any findings from its
standards/spec passes must be handled before declaring this slice complete.

## Execution record (2026-08-31, closeout continuation)

The skill inventory was rechecked before resuming this turn. Routing remains
explicit: `domain-modeling` and `writing-for-agents` for the status vocabulary
and this record, `tdd` for focused public-contract regression checks,
`playwright` for sequential desktop/mobile smoke, and `code-review` from the
fixed point `b83e380`. `diagnosing-bugs` is only activated if a short check
produces a concrete failure. `grilling`, `research`, `prototype`, and schema
migration skills remain deferred: no new product decision, external fact,
visual experiment, or persisted schema change is in scope.

The README and implementation backlog were corrected so clustering, conflict
marking, and the read-only relationship graph are recorded as implemented
review projections. The remaining gate is explicit GM acceptance of a fresh
reality-horror `p96-180` project, followed by later fantasy/general-prep work;
old artifacts and the retired incremental-card path remain out of scope.

This continuation performs only bounded verification. It must not launch a
long-running real LLM job or repeat the already completed `run.bat` cleanup.

Static checks and the focused regression set passed, including both JavaScript
syntax checks. Sequential Playwright smoke against the existing service on
port 8002 passed at 1440px and 390px: the empty workspace stayed unselected,
the real shelf loaded, the relation graph rendered a non-empty SVG, and there
were no browser console errors or narrow-screen overflow. The fixed-point
`code-review` standards and spec agents were retried once each, but both were
blocked by the upstream `429 Too Many Requests` limit before producing a
report. No code change was made in response to that external failure; a later
 review can be run from `b83e380` when the review service is available.

## Execution record (2026-08-31, semantic segmentation closeout)

The installed skill inventory was checked before resuming this bounded slice.
`diagnosing-bugs` supplied the verification discipline for the review-queue
usability risk, with focused contracts and a live browser path as the
red-capable feedback loop. `playwright` verified 339 accepted candidates:
50-per-page pagination, whole-filter selection across page changes, summaries,
and collapsed singleton clusters. Semantic segmentation and source-file
lifecycle contracts passed; `codebase-design` and `domain-modeling` continue to
define the semantic-window seam and candidate review projection.

Semantic segmentation is the primary strategy for new preparation jobs. The
planner must return an exact, non-overlapping partition of selected pages;
invalid or unavailable planning falls back to persisted mechanical windows, so
coverage is never silently lost. Existing jobs are not migrated or re-sliced.
The JavaScript regression was run with `node`, and all static checks passed.
The temporary `output/source-files-8003.json` fixture is removed during
closeout.
