# 0.1.3 Release Evidence And 0.1.3a Follow-ups

## Release Gate

The fresh reality-horror `p96-180` smoke test completed the full workflow. Semantic segmentation produced 14 semantic segments and 16 transport windows; every extraction and consolidation task reached a successful terminal state without page truncation or consolidation-budget failure. The reviewed workspace supplied 263 facts to artifact generation, which completed 3 local-digest batches, 94 local units, one global plan, and 47 materialized cards.

The run is sufficient evidence for the `0.1.3` release. The observations below are grouped for `0.1.3a` so the release does not mix a large Python/runtime migration and semantic-pipeline stabilization with another artifact-planning change.

## 0.1.3a Investigation Set

### Run-state and recovery presentation

- The artifact run recovered from one 300-second local-digest timeout and later completed by reusing successful steps.
- While the backend job was failed, the browser was observed showing `整板生成 · 等待开始`. Reproduce whether a long request failure can leave stale queued text after polling or page restoration. The expected UI always reflects the persisted terminal status and presents the existing unified retry action.
- Model/schema retries in the same run were recoverable and preserved attempt history. External provider availability failures remain environment evidence, not a product requirement or a reason for provider-specific workflow branches.

### Schema-valid card formatting

- The final cards for `卡莱尔公馆图书馆` and `约拿·肯辛顿` contain local formatting defects, although each complete card remains usable.
- Preserve the persisted responses as the repro evidence. Classify the exact malformed field shape before changing prompts or normalization; the current observation does not justify broad repair logic.
- Acceptance: the same field patterns render consistently without weakening unknown-field, unknown-fact, or field-source validation.

### Global plan card limit

- `MAX_DRAFT_CARDS = 50` came from the initial repository baseline and has no documented product decision behind it.
- The first global-plan attempt returned 53 valid planned cards and failed only because of this numeric limit. Its retry returned 47 cards by combining four expert NPC cards into one card and four expedition-member cards into one card. All referenced facts remained covered, but table-side retrieval granularity changed.
- Decide card grouping from independent table use, contactability, investigation value, and narrative function. A numeric limit may remain only as a generously sized technical safety bound; it must not force otherwise useful entities to merge.
- Acceptance: a plan containing at least 53 valid cards passes the planning contract, retains every planned fact closure, and is materialized without a single oversized model request.

## Known 0.1.3 Limit

`0.1.3` retains the current 50-card planning ceiling and the two observed formatting imperfections. Every generated card remains a GM-reviewable draft; neither observation caused source-fact loss in the accepted smoke result.

## 0.1.4a0 Implementation Status

- The artifact planner safety bound is now 120. It is documented in code as a technical guard and no longer acts as a reasonable-use grouping rule.
- Materialized card validation now flattens one `{value, field_sources}` wrapper emitted by some gateways. Unknown wrapper keys and deeper non-string scalar values remain hard validation errors.
- The browser ignores a stale non-terminal response when the same artifact job has already reached `completed` or `failed`, preventing an old queued response from replacing a terminal state.
- Semantic planning now asks for the smallest coherent preparation units and semantically refines a plan whose chapter-sized segment would own more than three transport windows. A failed or unchanged refinement preserves the original semantic ownership instead of mechanically cutting it.
- A timed-out reducer batch retries at smaller lossless candidate sizes to a bounded depth. Other failures retain their existing behavior, and the UI distinguishes completed transport windows from the semantic reduction result.
- Offline regressions cover these behaviors. Real upstream semantic-analysis and artifact smoke remain the release gate for `0.1.4a0`.
