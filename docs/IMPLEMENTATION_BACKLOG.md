# Implementation Backlog

Status: R1/R2, R3/R5, R4, both R6 slices, R7 shared interfaces, the two-board acceptance preparation, and the isolated synthetic rehearsal review are independently signed off in the current working directory, uncommitted. Real generation and human timing remain unverified.

The owner-confirmed goals and ruleset boundaries are in `PRODUCT_GUARDRAILS.md` (2026-10-31), with board direction in `REASSESSMENT_2026-08-31.md`. The current implementation audit, evidence, limitations, and R1–R7 acceptance criteria are in `MAINTENANCE_2026-10-31_TAKEOVER_AUDIT.md`. Historical projects and artifacts inform diagnosis; compatibility or migration requires an explicit user-facing rationale.

## Completed

- Two-board acceptance preparation (2026-10-31, signed off and synchronized): source-anchored
  synthetic samples, 20 GM lookup questions with 22 actual card/field mappings, honest answer
  boundaries and staged records. Review corrected itinerary clocks, unsupported expectations,
  missing optional material, ambiguous measures and a mapping check that could falsely pass.
  Evidence and limits: WORKFLOW_ACCEPTANCE_REVIEW.md. No production behavior changed in the
  preparation slice.

- Isolated synthetic workflow rehearsal review (2026-10-31): both source-only PDFs reached the
  real UI upload, Fake extraction, review/promotion, artifact approval, runtime lookup, saved
  preparation selection, and refresh recovery in a temporary database. A real runtime search
  also found an approved card omitted from the preparation selection. The successful run used a
  no-dot code copy; the documented candidate `serve.py` command does not reproduce it because
  `_pdf_files_under` hides the `.claude` path. Fantasy explicit restart, plan-excluded scene
  boundaries, multi-scene switching, content fidelity, and human timing remain unverified.
  Evidence and corrected claims: `WORKFLOW_REHEARSAL_REVIEW.md`.

- Shared generation interfaces (R7 slice 1, 2026-10-31, signed off and synchronized): the fact-input
  projection, budgeted batching and durable JSON step now live in `fact_input` and `persistent_step`,
  used directly by both artifacts and structure; `structure` no longer imports `artifacts` and no
  cross-module private helper remains. Step ids, hashes, attempts, bounds and artifact-specific
  plan-coverage recovery are unchanged, with the one documented exception that structure's oversized
  fact now raises its own generation error, still recorded as a failed job. Review fixes and
  evidence, including test isolation and real parent-step recovery, are in R7_SHARED_INTERFACE_REVIEW.md.

- Unreachable legacy runtime renderer (R6 retirement slice 2, 2026-10-31, signed off and synchronized):
  `renderRuntimeLegacy()` had no direct, dynamic, test, packaging or documented consumer, and its
  `#current-scene` node and `.runtime-grid` rules were exclusive to it, so both were removed. Live
  runtime rendering, scene switching, retrieval, preparation selection, continue, and the old-beat
  chapter/thread paths are untouched; no model field, endpoint, saved data, or generation path was
  deleted. Independent two-board synthetic browser and regression evidence, including limits, are
  recorded in R6_RETIREMENT_REVIEW.md and the audit.

- Workspace availability and failure reasons (R6 slice 1, 2026-10-31, signed off and synchronized):
  bookshelf status and opening share structural and board-contract validation; damaged projects remain
  identifiable by their storage row ID, including missing or mismatched body IDs. Direct-open and
  return-to-shelf failures preserve their actionable reason and navigation, while a successful project
  load clears the failure. No migration or repair occurs. Evidence and limits: R6_IMPLEMENTATION_REVIEW.md
  and the audit.

- Board-versus-rules boundary (R4, 2026-10-31, signed off and synchronized): generation messages carry
  only the board's structure and card contract plus one shared boundary statement, instead of the
  published game's name, "rule archive" summary, risk axes, GM/failure moves, prompts, and scene
  guidance. Narrative card types, sourced original-rule numbers, provenance, review, and preparation
  selection are unchanged; the runtime panel labels its guidance as a preparation-method reference.
  Review narrowed an overbroad numeric restriction so source-backed counts, times, dates and
  distances remain narrative content. Correction/cache/format-retry and sourced-rule checks pass.
  Evidence and limits: R4_IMPLEMENTATION_REVIEW.md and the audit.

- Saved material selection (R3/R5 production slice, 2026-10-31, signed off and synchronized): one current
  selection per project/profile stored in an optional `ExampleBundle.prep_selections` field through a
  narrow validated endpoint; the artifact page can browse, add, remove and explicitly save it, and the
  runtime page exposes approved selected cards next to retrieval. Plan membership, running state,
  provenance, and continue/reset boundaries are unchanged; stale references stay removable and never
  leak drafts. Evidence, field/entry rationale, and remaining limits are in the audit's R3/R5
  implementation records. In-flight edits survive; project/board drafts and responses are isolated;
  stored and first-seed writes preserve concurrent selections; current clocks have valid shortcuts;
  restart/reset confirmations state their effects. Independent synthetic browser, concurrent HTTP,
  regression and integration evidence, including limitations, are in R3_R5_IMPLEMENTATION_REVIEW.md.

- R3/R5 workflow prototype v2 (2026-10-31): signed off as a design artifact only after closing
  four P2s; actual DOM interaction evidence and limitations are in R3_R5_WORKFLOW_REVIEW.md.
  The production slice is separately signed off above.
- Runtime retrieval and related-person access (R2, 2026-10-31; independently signed off and
  synchronized): search, hits, and selected details sit beside the current scene. Retrieval and
  related details accept only explicitly approved cards; scene-held links handle actual clicks;
  unavailable scene switches are read-only with scope explanations. Approval-state and event-chain
  regressions, independent synthetic browser checks, integration safeguards, and remaining limits
  are recorded in MAINTENANCE_2026-10-31_TAKEOVER_AUDIT.md.
- Existing-project loading is decoupled from source-management metadata (R1, 2026-10-31, including the
  independent-review follow-up; independently signed off and synchronized to the main working directory): project material and session state render first, source details load
  in the background with their own loading/ready/failed state, and project-readiness and
  source-detail times are reported separately. An upload uses its own response to become selectable
  at once, so a slow or failing detail listing cannot hold the upload UI or block choosing the
  source. Measured cost, verification, and open follow-ups are in
  `MAINTENANCE_2026-10-31_TAKEOVER_AUDIT.md`; `scripts/report_source_metadata_cost.py` re-runs the
  measurement.
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

## Next Work

1. **Parameterize a reproducible rehearsal runner.** Preserve the existing product behavior while
   making the no-dot temporary root, code-copy manifest, forced Fake override, cleanup, and source
   PDF hashes runnable from one command. Add no production import or persistence path. Then review
   the cross-task single-candidate acceptance route with a task-ownership regression before any
   fix is proposed. Scope and evidence: `WORKFLOW_REHEARSAL_REVIEW.md`. Real model quality,
   plan-excluded scene boundaries, and the recurring preparation-time target remain unverified.

The independent general-preparation board remains deferred behind the two confirmed primary user
workflows. It must not become a mandatory conversion stage for other boards.

The earlier implementation of chapters, threads, beats, dispositions, and whole-approved-chapter
generation remains available for reuse. Passing offline contracts does not establish current long-form
artifact quality or the recurring preparation-time target; those require fresh output and actual use.

## Long-Form Artifact Quality (2026-09-13)

The first full-module fantasy build (*龙金劫重置版* p1-136, 100 cards across 6 reviewed
chapters) was reviewed. The scoring standard was corrected first: narrative content is
in scope, while numeric data and adversary type are not, so Daggerheart's Tier,
Difficulty, and adversary Type were withdrawn as missing items. Evidence, retractions,
and the proposed long-form gate are in
`MAINTENANCE_2026-09-13_ARTIFACT_QUALITY_REVIEW.md`.

The following is the earlier ranking from the 2026-09-13 review; its original selection is kept for traceability. Current work order is maintained in Next Engineering Work above, and these items' implementation status must be checked before reimplementing them:

1. **(selected)** Separate the three audiences that `open_questions` conflates. The
   owner's decisions are recorded in
   `MAINTENANCE_2026-09-13_ARTIFACT_QUALITY_REVIEW.md`: module-state anchors — "has this
   happened yet, and where in the timeline are we?", which dominate the measured 580
   questions — are not artifact content, because they depend on player actions that no
   module or tool can determine and every table differs. The pipeline stops emitting
   them instead of aggregating them. A new field carries a few table-side creative
   prompts; a second new field carries a declarative entry precondition, a source-derived
   statement rather than a question; and `open_questions` keeps only genuine source
   contradictions, terminology gaps, and unestablished framing. This gives "the source
   does not establish this" a legitimate place to live instead of pushing the model to
   invent a value for a required field.
2. Reshape environment and adversary features into named passive/action/reaction entries
   with a trigger and an effect. `usable_features` must stop absorbing reference lists;
   the existing short-adventure gate already forbids environments becoming longer
   generic summaries.
3. Runtime-group naming. Ten environment cards end with two or three groups because a
   cross-chapter merge unions group IDs, but the union is mostly legitimate: a card serving
   two chapters links to each chapter's support cards. The measured defects are elsewhere —
   one entity acquires several non-canonical variants (卡萨兰特庄园 carries
   `kasallant_manor`, `chapter0_cassalanter` and `cassalanter`), the chapter index leaks into
   names (`chapter0_xanathar`), and campaign-wide themes stand in for situations
   (`eye_recovery` is shared by 10 cards). Fix at naming time: one canonical semantic name
   per situation or recurring entity, reused across chapters.
4. `environment_type` scannability: it is narrative content and stays in scope, but the
   current free prose ("下水道追踪—藏身处潜入—魔法门入口局势") does not classify the
   scene at a glance. Move it toward a small set of scene rhythms while still allowing a
   compound description.

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

## Long-Form Artifact Gate

A module processed whole — many reviewed chapters, cross-chapter threads, and facts shared across
chapters — is accepted by these four criteria instead of the short-adventure gate above. Their
derivation, the evidence, and the two builds they came from are in
`MAINTENANCE_2026-09-13_ARTIFACT_QUALITY_REVIEW.md`.

1. **Completeness.** No runnable information in the module is lost. A module of this style is written
   for the GM to read whole and is not organised session by session, so processing it whole is the
   point rather than over-preparation. *Judged by*: every promoted fact carries a disposition, every
   populated field carries `field_sources`, and supplied numbers survive only as quoted source
   material in `source_statistics` and `source_statblock`.
2. **Retrievability.** A GM answers a question about the current scene in play without returning to
   the facts page. *Judged by*: questions per card stay low; content arrays hold a scannable number
   of short entries instead of reference lists; `environment_type` opens with one scene rhythm; and
   at the table the GM answers a question about an NPC's motive or an environment's danger signs from
   the runtime view alone. The counts come from the commands below; the table-side check is a human
   trial and cannot be automated.
3. **Honesty.** Content the source does not establish is not invented. *Judged by*: a sample of ten
   cards, read field by field against the facts each field cites, with each citation recorded as
   supporting, unsupported, or missing, and an accepted ceiling on the second and third. A required
   field the source does not establish states what the material assumes and records it in
   `assumed_state`. The deterministic validator only checks that a cited fact ID exists, so it cannot
   judge this criterion on its own.
4. **System boundary.** A card is never rejected for lacking an element of the target system. A D&D
   module having no Daggerheart tier or adversary type is normal, not an error. *Judged by*: zero
   cards rejected or retried for a missing target-system field, and every profile's required fields
   remaining narrative.

Measurements, both read-only and calling no model:

- `python scripts/report_artifact_quality.py [workspace_id]` — criteria 1, 2, and 4.
- `python scripts/report_group_canonicalisation.py [job_id]` — runtime-group identity under
  criterion 2, read from the identity clusters rather than from the raw rate.

The assessment of 2026-09-14 found the reviewed build passing 1 and 4, failing 2, and leaving 3
unverifiable until the ten-card sample above is read.
