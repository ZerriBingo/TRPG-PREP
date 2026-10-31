# Artifact Quality Review - 2026-09-13

Status: review record. Findings below are evidence-backed observations and proposed
criteria; the workflow changes they imply still need the owner's decision before
implementation. The chosen first work item is recorded in `IMPLEMENTATION_BACKLOG.md`.

## Subject

The reviewed build is the real generated artifact for workspace
`prep_job_1f988e14729a4da29e594fa0b4fe4af1`: the full text of *龙金劫重置版* p1-136
(162 source pages), profile `daggerheart`.

- 100 cards: 49 `environment`, 21 `npc`, 17 `enemy`, 13 `encounter_clock`.
- 6 reviewed chapters, 19 cross-chapter story threads, 19 chapter-local beats.
- 1978 promoted facts; 1617 of them carry a chapter disposition.

Provenance was verified before judging: the build came from the current pipeline,
not an earlier one. `artifact_job_8708b88d75194947a48eaa2df48311b7` completed with
305 steps — `local_digest` 22, `planning_index_3_0`/`_4_0`/`_5_0` 57/58/55 (single
round, no collapse rounds), `global_plan` 8 (two scopes retried), `global_plan_coverage`
1, `identity_reconcile` 4, `materialize` 100 for 100 cards with zero failures.
Cards report `prep-artifact-draft-v8` on `deepseek-v4.1-flash`.

## Scoring standard

The first pass graded the artifact against Daggerheart's full stat-block checklist
and recorded Tier, Difficulty, and adversary Type as missing. That standard was
wrong. Those fields are Daggerheart-specific adversary and encounter data — the
numeric and combat side — and this product does not handle them. The user's boundary
is explicit: narrative content is ours to process, numeric content is not.

The workable standard is Daggerheart's block minus the numeric, tier, and adversary
type fields:

| Daggerheart element | Artifact field | Result |
| --- | --- | --- |
| Name | `title` | present |
| Description / Motives & Tactics | `opening_image`, `narrative_function`, `motives` + `signature_actions` | present |
| Impulses | `impulses` | present |
| Potential Adversaries | `potential_adversaries` | present |
| Features | `gm_responses`, `triggers`, `phase_changes`, `exit_outcomes` | function present, shape absent |

`environment_type` is in scope: it tells the GM the rhythm of the scene and is
narrative content. `enemy_type` is out of scope: it is encounter-design data on the
combat side. Both the fantasy profile and `ARTIFACT_CONTRACTS.md` already keep
numeric fields out of the required set, so the boundary holds in the contract.

Under the corrected standard the real shortfall is one theme, not five: **the shape
of a feature**, plus the missing creative prompt.

## Findings

### 1. Feature fields became reference dumps

The gate in `IMPLEMENTATION_BACKLOG.md` already requires that environments expose
usable features, dangers, GM responses, phase changes, and exits "without becoming
longer generic summaries". The long-form build does not meet that condition.

Environment cards carry 14 field keys. `usable_features` on the merged chapter-one
card contains a paragraph listing roughly fifteen guilds and their fees; the tavern
card carries 13 such entries, several hundred characters each. A Daggerheart feature
is a named passive, action, or reaction with a trigger and an effect. The function is
present; the named, scannable shape is not.

### 2. No creative prompt

Daggerheart pairs each environment and adversary feature with an italicized question
that ignites play ("What trinkets and baubles lie along the bottom of the riverbed?").
The artifact has no field for this kind of prompt. Its `open_questions` are a
different kind of text, which is the subject of finding 3.

### 3. `open_questions` conflates two audiences, and its volume makes it unusable

Measured across the build:

- 580 questions total, on 100 cards, up to 19 on one card.
- 573 are unique; only 7 exact duplicates exist. **Duplication is not the cause.**
- Only 69 carry an explicit source-gap marker such as 未定 or 源中.
- 179 mention a PC or the players.
- Distribution: environment 313, npc 118, enemy 90, encounter_clock 59.

Sampling shows one dominant kind: **module state anchoring** — "has this happened
yet, and where in the timeline are we?" Examples: 是否已获得或交付金库黄金；贾拉索当前是否已知大博弈；卡萨兰特是否已从金库获得黄金；利夫当前是否已被安抚.

The need behind those questions is real — a D&D module of this style is non-linear with
many parallel optional events, so a GM must know where the party stands — but it is not
a need this product can serve.

The user's account matches the evidence: the mechanism started from the player-side
scaffolding direction and then produced an exhaustiveness ledger instead. Three
audiences were merged into one field:

| Audience | Correct form | Current state |
| --- | --- | --- |
| Table-side creative prompt | a few evocative prompts on the card | almost absent — this is finding 2 |
| Module state anchor | the GM's own record, outside the artifact | shredded into card annotations |
| Source contradiction or terminology gap | a review finding, which is genuinely ours | mixed in with the other two |

**Owner decision (2026-09-13a):** state anchors are not this product's content. Which
optional events occurred, and where a table stands in the timeline, depend on player
actions that neither the module nor the tool can determine; every table differs. These
are questions the GM records for their own game. The first draft of this review proposed
aggregating them onto a campaign-level state surface. That was wrong in kind, because it
still asked the tool to hold an answer it can never have. The consequence is that the
pipeline stops emitting them as card annotations at all, which removes most of the
580-item burden at its source rather than relocating it.

**Owner decision (2026-09-13b):** two fields follow from the same review.

- Table-side creative prompts get their own card field instead of overloading
  `open_questions`. The field regains its meaning, and artifacts and step caches that
  already exist are unaffected.
- Cards also carry a declarative entry precondition — a short statement such as
  "this material assumes the manor has not yet opened". It is derived from the source,
  asserts nothing about player action, and lets the GM match a card against their own
  record of what has happened. It replaces the interrogative form; it does not
  reintroduce it.

What remains in `open_questions` after that suppression is the part that is genuinely
ours: contradictions inside the source (瓦罗所述佛伦失踪时间线与本章后文不一致),
terminology gaps (各派系用不同术语指代“眼睛”的对应关系未列出), and unestablished framing
that changes how a card should be run. That residue is small and belongs in review.
Whether a card should instead state a **declarative** entry precondition — for example
"this material assumes the manor has not yet opened" — rather than ask a question is
still open.

A heuristic split of the 580 with deliberately narrow markers classifies 329 as state
anchors, 31 as source contradictions or gaps, and 220 as other. The "other" bucket is
not a fourth kind: its samples are mostly the same state anchoring phrased with a bare
是否 or with 为何/哪些 ("火球是否必然在3月22日发生？", "当前节庆季进度与已经发生的事件为何？").
The true state-anchor share is therefore higher than the marker count suggests.
Suppressing that kind removes roughly four fifths of the questions and leaves a short
review list of contradictions, terminology gaps, and unestablished framing — the part
that is genuinely ours — together with the new creative-prompt field.

### 4. Required narrative fields can force invention

Every fantasy card type's required fields are narrative, which respects the boundary.
But they must be non-empty, and there is no sanctioned "the source does not establish
this" value. When a D&D module genuinely omits something — an NPC's long-term goal,
an environment's narrative function — the model can only invent a value or fail
validation. That is the failure mode the owner described as cards being rejected and
the pipeline stalling.

This build did not stall: all 100 cards materialized, and 999 fields yielded 959
fields with a source citation and 40 without one, all 40 being `gm_responses`, which
the contract explicitly permits to be GM-authored without provenance. Given that
every other field carries a citation, the more likely explanation is that the model
filled unsupported fields anyway rather than that the module established all of them.
That is the more dangerous form of the same defect, because the validator checks that
a cited fact ID exists, not that the fact supports the claim. The build therefore
looks compliant while some narrative filler may be unverifiable.

## Retractions

- "Missing Tier, Difficulty, and adversary Type" — withdrawn. Numeric and
  encounter-design data is outside this product's scope.
- "Whole-module preparation conflicts with Daggerheart's week-by-week guidance" —
  withdrawn. That guidance assumes Daggerheart's own short campaign frames. Battle-tested
  D&D modules of 160+ pages are written for heavy GM reading and are not organized
  session by session, so processing the whole module in one build is the product's
  reason to exist, not over-preparation.
- "The 580 questions are mostly duplicates" — withdrawn. 573 of 580 are unique.
- "Environment `environment_type` should be a closed set because Daggerheart closes
  it" — downgraded. The field is in scope as narrative content, so the remaining
  argument is scannability only: the current values are free prose such as
  "下水道追踪—藏身处潜入—魔法门入口局势" and do not classify the scene at a glance.

## Proposed long-form gate

The short-adventure gate in `IMPLEMENTATION_BACKLOG.md` remains a regression baseline.
Long-form work needs its own criteria, as that file already anticipates:

1. **Completeness** — no runnable information in the module is lost. A D&D module must
   be processed whole. This build passes: 100/100 cards, no field missing a source
   key, source numbers preserved in `source_statistics` and `source_statblock`.
2. **Retrievability** — a GM finds what the current scene needs within seconds. This
   build fails: 580 scattered questions, 14-field environment cards, prose features.
3. **Honesty** — content the source does not establish is not invented. This build
   looks compliant but is not verifiable: citations are complete, yet the validator
   cannot judge whether a citation supports its field.
4. **System boundary** — a card is never rejected for lacking target-system elements.
   A D&D module having no Daggerheart tier or adversary type is normal, not an error.
   This build passes.

## Work items

Ranked; the owner selected item 3 as the first.

3. **(selected first)** Separate the three audiences that `open_questions` conflates.
   Stop emitting module-state anchors, which are the GM's own record and not artifact
   content; add a dedicated field for a few table-side creative prompts; and keep only
   genuine source contradictions, terminology gaps, and unestablished framing for
   review. This removes most of the 580-item burden at its source rather than
   relocating it, supplies the missing creative prompt, and improves card scannability.
   Whether cards should also state a declarative entry precondition remains open.
1. Reshape features into named passive/action/reaction entries with a trigger and an
   effect, and stop `usable_features` from absorbing reference lists.
2. Runtime-group naming. The first statement of this item was too simple. Ten environment
   cards end with two or three groups because a cross-chapter merge unions group IDs, but
   that union is mostly legitimate: a card serving two chapters links to each chapter's
   support cards. The measured defects are elsewhere — one entity acquires several
   non-canonical variants (卡萨兰特庄园 carries `kasallant_manor`, `chapter0_cassalanter` and
   `cassalanter`; 黑泪与克拉特之塔调查 carries `blacktear_tower` and `black_tears`), the
   chapter index leaks into names (`chapter0_xanathar`, `chapter0_kolatt`), and campaign-wide
   themes stand in for situations (`eye_recovery` is shared by 10 cards and `city_pressure`
   by 7). Fix at naming time: one canonical semantic name per situation or recurring entity,
   reused across chapters, with no chapter number, scope marker, or campaign-wide theme.
4. `environment_type` scannability: map it toward a small set of scene rhythms while
   allowing a compound description.

## Not judged

Narrative quality, and whether the proposed chapter and beat structure matches the
module's actual story arc. Both require a table run. The repository already states
that offline checks do not certify model-authored narrative quality.

## Implementation status

Work item 3 landed on 2026-09-13.

- The guidance sentence that caused the volume was the defect: `SOURCE_SEMANTIC_GUIDANCE`
  told the model to "record the uncertainty in open_questions" whenever the source did
  not establish a starting state. It now directs that case to `assumed_state` and states
  that which optional events occurred, and where a table stands in time, depend on player
  action and must never become review questions. `open_questions` is reserved for
  contradictions, terminology gaps, and missing framing inside the source.
- Two fields were added as **optional** content fields on every Daggerheart card type, so
  they are carried inside the existing `fields` map and neither the draft contract nor the
  derived-card model changed: `assumed_state` is a scalar declarative sentence and
  `table_questions` is a list, registered in `LIST_FIELDS`.
- `PROMPT_VERSION` moved to `prep-artifact-draft-v9`, which invalidates materialization
  steps as intended. `frontend/workbench.js` gained labels 前置状态 and 桌边创作提示.
- `scripts/test_state_anchor_contract.py` pins the contract, including that both fields
  must stay optional — a required field without source support is what pushes the model
  to invent a value.
- Suite state: 69 tests pass, ruff clean, the suite leaves the real database untouched.

**Not verified.** The offline fake fills required fields only, so no offline check proves
the model stops emitting state anchors or starts populating the two fields. That needs a
fresh real build, and the outcome should be judged by re-running this review's
measurements: the open-question count per card and the presence of `assumed_state`,
`table_questions` and the residue kinds.

Work items 1, 2, and 4 landed in the same pass, also as guidance rather than as new
machinery, because a hard cap would risk the stall this product must avoid:

- Array fields must stay scannable mid-scene. `usable_features` holds two to five named
  features, each one line pairing a trigger with its effect, and never an inventory, fee
  schedule, calendar, roster, or gazetteer; supplied reference detail of that kind belongs
  in `source_statistics`, and unsupported detail is left out because its facts stay
  retrievable. `impulses` stays at three to six short phrases, and `hazards`, `triggers`,
  and `phase_changes` stay equally short.
- `environment_type` begins with one scene rhythm — 探索, 社交, 穿越, or 事件 — followed by a
  short qualifier, so the scene's shape is readable at a glance.
- Runtime groups are named after the situation or recurring entity, with one canonical name
  reused across chapters, and with no chapter number, scope marker, or campaign-wide theme.

`PROMPT_VERSION` is now `prep-artifact-draft-v10` and `GLOBAL_PLAN_PROMPT_VERSION` is
`prep-artifact-global-plan-v16`. Both invalidate their caches as intended. The contract
assertions in `scripts/test_artifact_semantic_prompt_contract.py` pin all of it.

## Verification against a real build (2026-09-14)

The owner ran a fresh full build after the changes:
`artifact_job_c1badbe77eaa4860b9e351ddf6996c87`, workspace
`prep_job_443f516ce4d54e10910d8d353a02b25f`, 83 cards over 6 chapters and 1227 facts. This is a
different workspace from the reviewed build, so absolute counts differ; the comparison below is
per card and by kind.

| Measurement | Before (v8) | After (v10) |
| --- | --- | --- |
| Cards | 100 | 83 |
| Questions total | 580 | 150 |
| Questions per card | 5.80 | 1.81 |
| State-anchoring questions | 314 | 19 |
| Source contradictions and gaps | 31 | 39 |
| `assumed_state` filled | 0/100 | 83/83 |
| `table_questions` filled | 0/100 | 83/83 |
| `usable_features` entries per card | median 8, up to 19 | 5 (33 cards), 4 (1 card) |
| `usable_features` longest entry | 204 characters | 90 |
| `environment_type` led by a scene rhythm | 3/49 | 34/34 |
| Runtime group identifiers carrying a chapter index | 5 | 0 |
| Pipeline shape | `global_plan` 8 (2 retries), `materialize` 100 | `global_plan` 6 (no retries), `materialize` 83 |

`planning_index_4_0` and `_5_0` ran 43 and 46 times, single round as intended, and
`identity_reconcile` ran 4 times.

The residue improved in kind as well as in volume. The 96 questions the markers could not
classify are the part that is genuinely ours, and they are what a GM running a translated,
non-linear module would otherwise discover mid-session: 「五个参与派系」与「四个主要反派」的计数需
区分；Part 3、Part 3B/3C、Part 5E 等结构编号需统一；珊娜萨/珊娜莎译名混用；风爪之月 11 与 4 月 11
日、莱拉受难日与 4 月 10 日的历法对应需统一；仪式所需红玉四面体在源内有两种说法。Several of the
remaining state-flavoured entries now label themselves as per-table state, for example
来源未固定开场时雷纳是否已与 PC 接触；这属于各桌运行时状态，不应作为既定事件。

### Regression introduced and fixed

The runtime-group guidance in this pass caused an identifier regression. The new build produced
53 of 62 group identifiers as `encounter_situation_<hash>`, where the reviewed build produced
none. `group_id()` slugs a name with `re.sub(r"[^a-z0-9]+", "_", key)`, so a Chinese group name
slugs to nothing and falls back to the placeholder `situation`. The reviewed build happened to name
its groups in English keys (`tavern_night`, `eye_recovery`); instructing the planner to name a
group after the situation itself moved it to Chinese prose and collapsed every slug. Matching still
worked, because the hash carries the identity, but the readable `<slug>` hint the identifier
convention promises was gone. Measured identifiers after the fix should be re-checked on the next
build.

Fixed by requiring the entry itself to be ASCII, which is what "key" already implied before this
pass: `GLOBAL_PLAN_PROMPT_VERSION` is now `prep-artifact-global-plan-v16`, and
`scripts/test_artifact_semantic_prompt_contract.py` asserts the rule is present.

An audit of the other identifier generators explains why the prompt is the only correct lever here,
and records a trap. `_identity_keys_from_clusters` builds `identity_<slug>_<digest>` with a
Unicode-aware class and carries the comment `\W is Unicode-aware, so a Chinese label keeps its
characters instead of collapsing to the "shared" fallback an ASCII-only class produced` — the same
defect class was already met and fixed there, and it deliberately keeps Chinese. The two cases are
constrained differently rather than inconsistently: an identity key is internal and unvalidated,
while `runtime_group_ids` is validated by `re.fullmatch(r"encounter_[a-z0-9_-]+", item)` and
`plan_id` by `^plan_[a-z][a-z0-9_-]*$`, so those identifiers must stay ASCII and a non-ASCII source
name can only fall back. **Do not "fix" `group_id()` by making it Unicode-aware**: the resulting
`encounter_<Chinese>_<hash>` would fail that validator and the card would be rejected, which is the
stall this product must avoid. The ASCII requirement belongs in the planner contract, where it now
lives.

### Live verification of the fix (2026-09-14)

`scripts/check_group_naming_live.py` drives the real production path — structure job,
approval, artifact job — against an isolated temporary database. Local digest and global plan
call the configured model, because runtime groups are the planner's output; materialization is
delegated to the fake client since the plan owns runtime grouping. It spends real model calls, so
it is opt-in behind `TRPG_LIVE_TESTS=1`.

Result: `STRUCTURE_STATUS completed`, `ARTIFACT_STATUS completed`, 5 chapters and 12 threads
approved, 8 distinct group identifiers, **0 placeholder slugs**. The planner emitted readable
ASCII keys — `candle_lane_warehouse`, `dockyard_gang_conflict`, `old_sewer_routes`,
`sewer_magic_door`, `spring_festival_parade`, `harbor_priest_donations`, `west_dock_patrol_gap`,
`ledger_gray_hand_investigation` — so identifiers read as `encounter_candle_lane_warehouse_60101e7c59`
rather than `encounter_situation_...`, and none carries a chapter index.

The fixture also exercised the canonicalisation half of work item 2 on a small scale: an
environment card and the adversary supporting it share one key (`candle_lane_warehouse` appears on
both), and one adversary carries two keys for the situations it serves
(`old_sewer_routes` with `sewer_magic_door`).

**What this does not prove.** The fixture is small, so it shows readable keys and agreement between
an environment and its support card, not that one canonical name is reused across chapters of a
long module. That still needs a full build, measured as the number of distinct keys mapping to the
same situation.

A practical note for future live checks: a temporary database carries the default LLM settings,
which name a placeholder model and hold no key, so a real call fails with HTTP 401. The script now
copies `base_url`, `api_key`, and `model` from the real configuration into the temporary database
and never prints the credential.

### Measurement correction

An earlier count reported zero chapter-index leakage for both builds. That was wrong: the pattern
tested the start of the identifier, which always begins with `encounter_`. Searching the whole
identifier gives 5 for the reviewed build and 0 for the new one.

### Runtime-group canonicalisation: measured cause (2026-09-14)

The owner asked whether the many environment cards titled "甲与乙" are normal. They are the model's
own naming, not a code artefact: `artifacts.py` contains no title concatenation, `_merge_match`
matches on title first and identity second, and a merge only unions `fact_ids`, `runtime_group_ids`,
`chapter_ids`, and `story_beat_ids` while keeping the survivor's title. All 71 planned environment
cards carry distinct titles, and every saved card's title resolves to exactly one planned card.

The measurements show what actually happened, and it is not title matching:

| Stage | Environment cards | Groups per card | Notes |
| --- | --- | --- | --- |
| After planning validation | 71 | `{"1": 71}` — exactly one each | the one-situation rule holds |
| After merging | 34 | `{"1": 16, "2": 10, "3": 5, "4": 1, "5": 2}` | 18 cards carry two or more |

Across all four card types, 185 planned cards became 83 saved cards, and 45 of the 83 span more
than one chapter. Because **no title repeats**, not one merge was a title match: every merge went
through `identity_key`. A card planned as one situation with 7 facts and one group ends up with 122
facts, five groups, and four chapters.

**Structural root cause.** Planning is per chapter, and each request carries only that chapter's
context, so the planner cannot know the group name another chapter already used for the same
recurring situation. Cross-chapter vocabulary can therefore only be settled after
`identity_reconcile`, not by instructing the planner harder. `_identity_keys_from_clusters` already
derives `identity_<label>_<digest>` from a per-cluster `label` the model returns, so a canonical
vocabulary for each recurring situation already exists in the pipeline at no extra model cost.

**The tension that makes this a decision rather than a bug fix.** An environment card ideally holds
one key, because the card is the situation. But the union currently sustains retrieval: a support
card in another chapter carries that chapter's variant key, so collapsing the environment card to
one key would break the environment-to-support link unless the support cards are remapped too.
Because `group_id()` is a pure function of the name, remapping is a local computation once an alias
map is known.

Directions, in the order the owner may prefer to weigh them:

1. **Settle vocabulary at reconciliation.** Reuse the existing cluster `label` as the canonical key
   and remap the cluster's group IDs to it, including support cards, via an alias map.
2. **Constrain environment cards only.** After merging, keep one key on an environment card and
   leave support cards with several, accepting that cross-chapter links may weaken.
3. **Measure first.** The v15 build this analysis used was the least favourable case for
   canonicalisation, because Chinese group names collapsed every slug to the placeholder and no key
   could match any other. The v16 ASCII-key requirement landed after it. A further build should
   count, per situation, how many distinct keys map to it before any merge change is designed.

**Token cost of direction 1 is negligible, and it needs no new request.** The reconcile stage
already returns a `label` per cluster, so the canonical vocabulary is on hand. Folding an alias map
into that same response adds a few dozen output tokens per reconcile chunk — a handful of calls per
build, each already bounded by `IDENTITY_GROUP_CHAR_BUDGET` — which is a few hundred tokens against
a planning stage that moved on the order of 1.5 million characters. Remapping itself is local. If
canonical keys make the union collapse, the plan payload sent to materialisation gets slightly
smaller, so the net effect can be a small reduction rather than a cost.

#### Measured against ground truth (2026-09-14)

`scripts/report_group_canonicalisation.py` measures the question directly. The identity
reconciliation stage already clusters the plans it believes describe one recurring situation, and a
cluster is ground truth for "one thing", so the number of **distinct runtime-group keys inside a
cluster** is exactly the canonicalisation failure count. The script is read-only and calls no model.

| Measurement | Reviewed build (v8) | New build (v10/v15) |
| --- | --- | --- |
| Planned cards → saved cards | 177 → 100 | 185 → 83 |
| Identity clusters | 97 | 75 |
| **Clusters whose members share one key** | **69/97 (71%)** | **31/75 (41%)** |
| Environment cards with more than one group | 10/49 (20%) | 18/34 (53%) |
| Cards spanning more than one chapter | 22/100 (22%) | 45/83 (54%) |
| Placeholder slugs | 0 | 53/62 |

Cluster spread for the new build is `{0:6, 1:25, 2:18, 3:18, 4:4, 5:3, 6:1}` against
`{0:6, 1:63, 2:16, 3:7, 4:4, 6:1}` for the reviewed build: the new build's clusters are both larger
and more heterogeneous.

Three cautions about reading this table.

- **It is not a controlled comparison.** The two builds ran on different workspaces with different
  fact sets (1617 versus 1227) and different chapters, so the difference mixes the contract change
  with the material.
- **The placeholder collapse does not by itself break matching.** `group_id()` hashes the name, so
  two chapters that used the same Chinese name still produce the same identifier and the union still
  collapses. The collapse costs readability, not identity. That means the 71% → 41% movement is not
  explained by it and needs its own explanation.
- **One plausible explanation is this pass's own guidance.** v15 told the planner to name a group
  after the situation itself and to reuse one canonical name, which may have produced more
  descriptive and therefore more chapter-specific phrasing than the earlier "short semantic key"
  wording. v16's ASCII-key requirement landed after this build and has not been measured at module
  scale.

The shared-stem families the script reports are a weak signal and should not be read as duplicate
keys. In the reviewed build `trollskull` covers four genuinely different things
(`trollskull_alley_kids`, `trollskull_alley_shops`, `trollskull_room`, `trollskull_tavern`), while
`chapter0` is the chapter-index leak already identified. The cluster spread, not the stem grouping,
is the measurement that answers the question.

**Normalising for cluster size corrects the headline above.** A bigger cluster has more chances to
show variance, so the reconcile stage's aggressiveness must not be read as naming variance. The
report now also prints the one-key rate by cluster size:

| Cluster size | New build (v10/v15) | Reviewed build (v8) |
| --- | --- | --- |
| 2 members | 4/20 (0.20) | 5/18 (0.28) |
| 3 members | 3/12 (0.25) | 0/6 (0.00) |
| 4 members | 0/6 (0.00) | 1/4 (0.25) |
| 5 or more members | 1/10 (0.10) | 0/3 (0.00) |

At equal cluster size the two builds are comparable, so the raw 71% → 41% movement was a
composition artefact: the new build produced 48 multi-member clusters against the reviewed build's
31, meaning reconciliation grouped plans more aggressively rather than naming becoming more
chapter-specific. The earlier reading in this section, which treated the drop as a canonicalisation
regression, was wrong.

What survives, and it is the more important finding, is the absolute level. **In both builds a
shared key for one identity is the minority**: at cluster size two only 20–28% of clusters agree on
a key, and at size three and above the rate is often zero. Divergent keys for one identity are
therefore the norm rather than a regression, which is why merging almost always unions variant keys
and why an environment card ends up carrying several. The "甲与乙" titles the owner noticed are the
visible surface of a systemic property, not of the v15 change.

**Next measurement.** Re-run this report on the next full build and read the size-normalised table,
not the raw rate, to see whether v16's ASCII-key requirement raises the one-key rate. If the
size-normalised rate stays near these values, the merge is joining differently-named situations and
direction 1 becomes justified; if it recovers, no merge change is needed.

## Pre-build fixes (2026-09-14)

Three items were settled before the next full build.

### Module commentary was becoming a chapter, and then environment cards

Confirmed, and it is a genuine defect. The new build's first chapter is 准备与结构校准 with 104 facts,
and its facts are commentary about the module rather than its fiction:

- 重置《龙币之劫》有三个主要目标：1) 包含一起或多起抢劫；2) 去掉原本“选一个反派”噱头…
- 三线索原则（Three Clue Rule）要求模组对同一个目的地设置三条不同方式的线索…
- 重置版不会逐一改掉情境中的所有连续性错误…并相信 DM 能使用宽泛参考文件作为标准…

The chapter summary states it plainly: 本章只做准备，不规定固定路线. The artifact stage then turned
it into environment cards, including 准备与结构校准：重置目标与三线索 carrying 117 facts, plus
大博弈派系布局与五方竞争, 抢劫循环与戈罗尔之眼争夺, and 哨站、响应队伍与调查循环. None of those is a
situation a GM can run at the table.

The machinery to place such facts correctly already existed: `fact_dispositions` accepts
`global_reference`. The disposition guidance only said that general rules commentary *may* remain
global reference, which is permission rather than prohibition, so the model made a chapter from it
anyway. The guidance now states that facts describing the module rather than its fiction — design
goals, revision notes, creator commentary, and preparation instructions — are not a playable
chapter, that no chapter key may be created for preparation guidance alone even when several such
facts share a topic, and that every chapter must contain material the PCs can encounter in play.
`STRUCTURE_PROMPT_VERSION` moved to `adventure-structure-v7`, and
`scripts/test_structure_disposition_contract.py` pins it.

### A required field may now state an assumption

Finding 4 recorded that a required narrative field has no sanctioned "the source does not establish
this" value, so the model can only invent or fail validation. The materialisation guidance now says
that when the source does not establish a required field the model states what the material assumes
rather than inventing detail, and records that assumption in `assumed_state`. `PROMPT_VERSION` moved
to `prep-artifact-draft-v11`.

The limitation recorded in Finding 4 still stands: the validator checks that a cited fact ID exists,
not that the fact supports the claim, so no offline check can confirm this behaviour. The next build
should read the fields rather than trust the rule.

### Verified as already correct

The two new fields reach the interface without further work. `frontend/workbench.js` renders card
fields generically through `Object.entries(card.fields)` in both the review view and the runtime
view, the runtime skip list does not name either field, and an unmapped key falls back through
`fieldLabels[key] || … || fallback`. No whitelist blocks them.

### Backlog reconciled

`IMPLEMENTATION_BACKLOG.md`'s `## Next Engineering Work` still listed four items that the fantasy
slice had already implemented and that `## Fantasy Slice Implemented` also described. It now carries
only the genuinely open item, the independent general-preparation artifact contract, with a note
explaining the removal.

### Upstream switched to a local proxy, and a null content field crashed the client

The previous upstream became unavailable and was replaced by a local Command Code reverse proxy
serving OpenAI-compatible endpoints on `http://127.0.0.1:3050`. It turned out to be callable without
any code change: `chat_url()` already maps a bare host to `/v1/chat/completions`, `ConfigIn` accepts
an `http://` URL, and the app's own client reached it on the first attempt, returning `{"ok": true}`
for a probe completion and 69 models from the authenticated list.

The real obstacle was the selected model. `meta/muse-spark-1.3-contributor` appears in the model list
but answers with a **null `content` field**, and `LLMClient.chat` returned that `None` verbatim, so
`chat_json`'s empty-response check crashed on `None.strip()` with
`AttributeError: 'NoneType' object has no attribute 'strip'`. The operator saw an opaque crash rather
than the message the code already knows how to produce. `chat` now maps a null or missing content
field to empty text, so the existing report — 模型连续两次返回空响应（请检查 base_url / 模型名 /
中转站是否可用） — is what surfaces, and `scripts/test_llm_null_content.py` pins it.

The application configuration now points at the proxy: `base_url` normalised from the full
`…/v1/chat/completions` endpoint to `http://127.0.0.1:3050`, `model` set to
`deepseek/deepseek-v4.1-flash`, which is the model the previous builds used and therefore keeps the
gate baseline above comparable, and the key supplied from the proxy's own `config.json`. The
authenticated list also offers `deepseek/deepseek-v4-flash` and `deepseek/deepseek-v4-pro` if a
different model is wanted.

### The model that looked broken, and what thinking actually costs

The operator reported that the switched upstream still could not be used, and explained the model
choice: `meta/muse-spark-1.3-contributor` was picked because it is cheap under testing load. That
reframed the earlier conclusion in this section. The `content: null` seen first was produced by this
review's own probe, which asked for it with `max_tokens=64`; muse is a reasoning model, so its
thinking consumed the whole budget and `finish_reason` came back as `length`. Given a normal budget
the same model answers correctly in three to five seconds.

Two real defects were behind the operator's experience, and both are fixed.

- **The empty response was undiagnosable.** `llm_worker.py` returned only `content`, so the app could
  not tell "the model spent its budget thinking" from "the upstream is unreachable". It reported
  模型连续两次返回空响应（请检查 base_url / 模型名 / 中转站是否可用）, which points at the wrong thing
  entirely. The worker now returns `finish_reason`, and `chat_json` reports a budget exhaustion
  distinctly and without a pointless retry.
- **Several stages had too little headroom for a reasoning model.** Budgets of 2500 to 4000 tokens
  were enough to be consumed by thinking before any content appeared. Every sub-6000 cap in the
  current pipeline was raised to 8000, matching the cap the worker already used by default.
  `max_tokens` is a ceiling rather than a reservation, so unused headroom costs nothing.

Measurements on the thinking controls, five runs per variant on a trivial prompt, output tokens:

| Variant | Mean | Median | Runs |
| --- | --- | --- | --- |
| OpenAI route, default | 346.6 | 361 | 255, 420, 361, 332, 365 |
| OpenAI route, `reasoning_effort=low` | 183.6 | 149 | 201, 137, 85, 149, 346 |
| Anthropic route, default | 458.6 | 304 | 280, 791, 675, 243, 304 |
| Anthropic route, `thinking:disabled` | 309.0 | 338 | 363, 341, 283, 338, 220 |
| Anthropic route, `thinking.budget_tokens=512` | 201.8 | 215 | 165, 215, 217, 189, 223 |

Reading it, with n=5 and visibly wide spread:

- `reasoning_effort=low` on the OpenAI route — the route this tool already speaks — roughly halves
  muse's output tokens, and its values separate almost completely from the default's. This is the
  lever worth having. `reasoning_effort=none` is rejected with HTTP 400, so the value must be
  constrained to what an upstream accepts and sent only when configured.
- The Anthropic route is not cheaper. Its default is the noisiest variant of all and its
  `thinking:disabled` overlaps that default heavily, so nothing here justifies teaching the client a
  second wire protocol.
- `thinking.budget_tokens=512` does appear to cap thinking, but only on the route we do not speak.

### The reasoning-effort knob, implemented

The optional control is now in place. `reasoning_effort` is a configuration field; accepted values
are `low`, `medium`, and `high`, and an empty value means the parameter is never sent, because an
upstream that does not recognise a value rejects the whole request with HTTP 400. `PUT /api/config`
returns 422 for anything else, `LLMClient` forwards the value to the worker, `llm_worker.build_payload`
adds it only when set, and `scripts/test_reasoning_effort.py` pins all three.

The application is configured with `meta/muse-spark-1.3-contributor` and `reasoning_effort=low`, which
the measurement above shows roughly halves that model's output tokens. The server was restarted so the
new code is loaded, and real calls through the app's own client return valid JSON.

Two notes for follow-up. The settings interface does not expose the field yet, so it is set through
the API; and the value is per-configuration rather than per-model, so switching to a model whose
upstream rejects `reasoning_effort` requires clearing it by hand.

### Muse's first real run: two defects found and fixed

The operator's first build on muse produced cost and reliability data, and two fixes followed.

**The assignment stage failed on closure.** `structure_job_f44bb6b7…` ended
`事实结构归属不闭合: missing=[8 fact ids], extra=[…]` in phase `assigning_facts`, after the step
runner's own correction retry, so muse did not cover every fact it was given. The same code closed on
1473 and 1978 facts under deepseek, so the machinery was not the limit; a cheaper model's instruction
following was. Three changes make the stage recoverable instead of fatal:

- Closure is now checked after the per-item work, so the assignments that did arrive are kept
  instead of the whole batch being discarded.
- The caller asks once more, in its own request, for only the facts that are missing.
- Anything still unassigned is deferred with a stated reason, which the application already
  supports, rather than failing the whole structure job.

`scripts/test_structure_assignment_recovery.py` pins both the repair and the deferral.

**The fact count dropped to roughly a quarter, and page coverage did not.** The operator noticed muse
produced about one fifth the facts and hypothesised a segmentation difference. Measured across the
three bundles:

| Build | Facts | Distinct pages | Page span | Facts per page |
| --- | --- | --- | --- | --- |
| muse | 334 | 133 | 1–136 | 2.51 |
| deepseek-v4.1 | 1473 | 135 | 1–136 | 10.91 |
| deepseek-v4.1 | 1978 | 135 | 1–136 | 14.65 |

The span and the coverage are the same, so nothing was skipped wholesale: muse produced coarser facts
from the same pages. That supports the hypothesis. What remains unverified is whether coarser facts
bundle away detail, which would surface later as thinner cards rather than as missing pages.

**The reasoning-effort control reached the interface**, as a dropdown offering 不发送 and the three
accepted values in the model-connection panel. The frontend is served by `StaticFiles`, so a browser
refresh is enough and no rebuild is involved.

### The reasoning-effort hint could break a run, and the owner restated the principle

Switching the configured model back to `deepseek/deepseek-v4.1-flash` left the reasoning hint at
`high`, which that model honours by spending the entire reply thinking: every call returned
`finish_reason=length` with no content, so analysis could not start at all. The hint is stored per
configuration rather than per model, so changing the model silently carried it over.

This was a design error in this pass, and the owner restated the rule it violates: whether the model
a user runs is capable is not something the product controls, and a user who cannot produce artifacts
concludes the program is broken. Blaming a weaker model is not an acceptable explanation, and the
program has to hold up across model choices. The guardrail is recorded in `PRODUCT_GUARDRAILS.md`.

The fix makes the hint self-healing rather than load-bearing. When a reply is empty with
`finish_reason=length` and a hint was sent, the client drops the hint for that client, repeats the
call immediately, and reports what it did. Because `RETRY_ATTEMPTS` is 1, the repeat happens inline
rather than through the retry loop, which an earlier attempt at this fix consumed by accident. A real
call with the exact broken configuration — `deepseek/deepseek-v4.1-flash` with `reasoning_effort=high`
— now returns valid JSON.

### Why analysis could not start: a false message over two real bugs

The owner switched back to `deepseek/deepseek-v4.1-flash` and analysis still would not run, reporting
that the upstream was unavailable. The report was wrong, and the chain behind it was traced.

**The recorded failure was an internal validation error, not an upstream reply.** All 47 windows of
`prep_job_30a255f8…` failed identically:

```
error: provide exactly one of raw_response or transport_error
kind:  upstream_unavailable     attempts: 0
```

That message comes from `shadow.submit_shadow_result` refusing an empty `raw_response`, so the real
cause was replaced by a sentence about a parameter, and the workbench then rewrote the window's kind
into 上游服务暂时不可用. A user reading that has no way to reach the actual problem.

**Two real defects were found and fixed.**

- **The worker subprocess could die on encoding.** `llm_worker.py` printed its result frame with
  `ensure_ascii=False` into a console code page that is GBK on Windows, so a reply containing certain
  characters raised `UnicodeEncodeError: 'gbk' codec can't encode character '\xc1'` and the call
  failed for a reason unrelated to the upstream. Reproduced directly. The frame is now ASCII-escaped,
  which is lossless because JSON escapes decode back to the same text and survives any code page.
  This is the most likely source of the intermittent failures across the whole session.
- **The analysis reply budget was too small.** Reproduced against real page text: a 56,895-character
  extraction prompt returned `finish_reason=length` at every reasoning setting, so the model's JSON
  was cut off mid-answer. The stage now asks for 32000, which this upstream accepts up to 64000, and a
  ceiling costs nothing when unused.

**What is still open, stated plainly.** Even at 32000 the reproduction still ends in `length`, and one
run returned empty content, so a larger budget alone does not make this stage complete. Bounding the
per-window extraction, rather than only enlarging its reply budget, is the next design step and has
not been done. Two diagnostic gaps also remain: the prep job records no job-level error, only
per-window ones, and the workbench's `userFacingError` rewrites any message containing 上游 into an
upstream-availability message, which hides precise causes such as these.

Suite state after these changes: 73 tests pass, ruff clean, and the suite still leaves the real
database untouched.

### The timeout that made normal use impossible

Once the error message stopped lying, the real failure was visible: `API 请求超时`. Two defaults had
drifted apart in `llm.py`:

| Process | Timeout when the caller says nothing |
| --- | --- |
| Parent, waiting on the worker subprocess | `request_timeout or 120`, plus 10 → 130 s |
| Worker, waiting on the upstream reply | `request_timeout or 240` → 240 s |

The parent killed the worker at 130 seconds while the worker would still have waited 240, and the
analysis stages pass no explicit timeout, so they hit it by construction: **any call slower than
130 seconds failed with a timeout the upstream never caused**, and the message named 120 seconds.
The parent now waits `DEFAULT_READ_TIMEOUT` plus a margin, the constant carries a note that the
worker mirrors it, and the timeout message reports the value actually used. The extraction stage also
asks for 600 seconds explicitly, because window extraction with a reasoning model can take minutes.

This one is fixed structurally rather than by observation: the constants and the call chain were
checked, but no call longer than 130 seconds was run to watch it succeed. The next analysis attempt is
its proof.

A measurement note for this repository: `REAL_DB_CHANGED` is only meaningful while the application is
stopped. A run with the server up reported the flag as true, and stopping the server showed the suite
leaves the database untouched; the change came from the running application's own activity.

### One malformed candidate discarded a whole window

With the timeout fixed, the run reached further and exposed the next defect. The window covering pages
102–111 failed on every attempt with:

```
error_kind = model_format
candidates.60.text  Field required [input_value={'confidence': None}]
candidates.60.kind  Field required [input_value={'confidence': None}]
```

The model emitted candidate 60 as an object carrying only a `confidence` field. Strict validation
rejected the whole response because of it, so every other candidate extracted from those 12,099
characters was discarded with it, and the job stopped at `partial` with that one window outstanding.

`_normalize_prep_response` repairs `kind`, `source_refs`, and `confidence` before strict validation,
but never checked `text`, so an entry with no text reached the validator and took the window down.
Entries without usable text, or without a usable kind, are now dropped individually. The raw response
stays in the run summary, so nothing is unrecoverable: it can still be read back from there.
`scripts/test_candidate_normalization.py` pins the drop, the survival of well-formed candidates, and
the pass-through of a response that is not a candidate envelope.

### The retry branch was unreachable, so a rate limit failed a window

After those fixes the same job ran with 45 of 46 windows succeeded, where every window had failed
before. The one remaining window, pages 102–111, failed on a genuine upstream rate limit:

```
HTTP 429
{"error":{"message":"Empty response from upstream (zero output tokens)",
          "type":"rate_limit_error"},"retry_after":10}
```

The upstream stated its own remedy — wait ten seconds — and the client ignored it. `RETRY_STATUSES`
contains 429, but `RETRY_ATTEMPTS` was 1, so the guard `attempt < RETRY_ATTEMPTS - 1` evaluated as
`0 < 0` and the retry branch could never run. The code was dead, and a transient condition therefore
failed a whole window of source text.

`RETRY_ATTEMPTS` is now 3, the wait an upstream states is honoured (read from the response body, with
the worker also capturing a `Retry-After` header), and it is bounded by `MAX_RETRY_WAIT` so an
unreasonable value cannot stall a run. A non-retryable status still fails immediately.
`scripts/test_llm_retry.py` pins the wait, the retry, and the immediate failure.

Across these four fixes the reported failure moved one layer at a time — upstream unavailable, then a
timeout, then a format validation error, then a rate limit — and each layer was the real cause that
the previous message had hidden.
