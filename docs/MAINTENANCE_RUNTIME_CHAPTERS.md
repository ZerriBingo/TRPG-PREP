# Chapter Runtime Stabilization

The runtime surface now uses reviewed chapters and their summaries. Structured fantasy builds no longer publish a duplicate package brief. Chapter reference fact IDs are retained on the chapter and must belong to its fact membership.

Chapter selection filters environment and supporting cards without prescribing a route. A beat belongs to exactly one chapter and selects applicable story threads for that chapter's games. Threads may cross chapters without being classified as main or sub. Skeleton and domain validation reject thread references outside a beat's chapter applicability.

Chapter planning explicitly links environment cards to applicable chapter-local beats. Shared thread or fact membership alone does not create a link. Runtime shows linked environments first and offers other same-chapter environments in a separate expandable list. Missing links are exposed as review questions, not filled by assigning every environment to every beat. Existing cards without links remain available as chapter references. Fresh generation is needed to evaluate these explicit suggestions; identifier validation cannot establish narrative quality.

Runtime source metadata stays in source details. Chapter and global reference material remain available alongside the narrative summary. Reference-only counts distinguish material not used in cards or summaries; counts are not a quality judgment.

Fresh smoke acceptance:

- Each newly generated beat describes per-thread changes and what remains open, plus how those changes interact. The same thread may develop differently across several beats; thread titles alone are insufficient.
- Environments are planned from independently runnable source situations before being associated with beats. Neither one card per beat nor a mandatory multi-environment ratio is a target.
- Structure review and runtime both show beat developments and the distribution of each thread across the chapter's beats. Existing structures without this content stay readable and show missing development notes; new content requires regenerating structure, not merely cards.

- All chapters finish generation and retain individual summaries and reference lists.
- Chapter navigation shows the applicable environments and support, and beat navigation can return to all environments.
- Reference-only material is reviewed for actionable leads, motives, and changes that should have been included in cards. Passing fact closure alone is insufficient.
- No raw fact IDs or provenance mappings appear as summary prose.
- Desktop and mobile layouts remain usable, and reality-horror chapter overviews remain available.
- Malformed provenance fails clearly; source-statistics strings preserve supplied text without inventing game data.

## Narrative Use And Source Form

Source form and preparation use are separate concerns. A letter or other handout with
actionable information may have a chapter disposition; it remains a source asset,
not a newly authored player handout. Asset-only dispositions retain a runtime source
reference even before an explicit display-material record is created. Reusable
capabilities that change investigative choices belong in applicable chapter preparation,
not solely in general background.

New structure proposals require each introduce/develop/resolve chapter role to have
a corresponding beat developing that thread. Background roles do not require a beat.
This checks relational coverage, not the quality of a proposed development; models
must not add empty mentions merely to satisfy the check.

Source play reports and optional examples retain their status during local observation,
planning, and materialization. Reported player decisions and victories do not establish
the current group's starting state or inevitable results. A clock represents one changing
pressure, with related advancement conditions and consequences; rooms, clues, and
historical evidence are not automatically consecutive stages.

Runtime support uses the selected beat's chapter when no explicit chapter is selected.
This does not require support cards to belong to a beat, or prescribe an encounter roster.

These generation changes use new prompt versions. Existing completed structures and
cards remain untouched; evaluating the new semantics requires a fresh downstream build.
Data reset is a separate explicitly authorized operation. Chapter-overview content review
remains an open workflow decision; this maintenance does not introduce another approval step.

The bounded planning-index summarizer preserves the same source qualifiers and pressure
semantics as local digests and card planning. Its `planning-index-v3` cache version prevents
reuse of summaries generated without those instructions.

A chapter scope no longer sends one disposition record per fact. Those records cost about
110 characters each and, in a six-chapter workspace, put four of the six scopes over the
request budget before any index prose was considered. The same thread and cross-chapter
roles are now sent transposed, one record per thread or per other chapter, together with
`chapter_fact_ids` naming the facts the request must account for. No fact or role is dropped;
the largest scope fell from 144,457 to 74,555 characters against the 120,000 budget.

Offline verification covers semantic segmentation/consolidation/recovery, reality-horror
runtime, independent display materials, chapter generation, full multi-chapter artifact
generation (including an actionable handout), bulk approval, cleanup, and runtime selectors.
These checks do not certify model-authored narrative quality. The next fresh smoke must
inspect example-vs-default state, actionable reference placement, meaningful thread
development, and whether clock stages describe one genuinely changing situation.

The single-chapter beat schema requires rebuilding old adventure structures. For the authorized clean test, back up the database and reset only the selected task's downstream structures, cards, plans, runtime state, and generation caches; preserve promoted facts and upstream review data. Do not reinterpret old multi-chapter beats as valid new structures.

## Chapter Planning Inputs

Artifact local digests are owned by an approved chapter and read only its applicable facts. A fact shared by chapters can be read in each chapter, but a digest is never reused by trimming its IDs while retaining unrelated prose. This may require more local requests than whole-workspace batching.

Reviewed global reference facts remain in the workspace and runtime reference surface. They are not appended to the first chapter or assigned a second time by artifact planning. Actionable multi-chapter material belongs to the applicable chapters during structure review; reference classification must not be used to satisfy an input budget. Chapter and thread summaries supply narrative context without authorizing new citations.

Planning requests encode fact IDs as request-local short identifiers and decode responses before validation/storage. The translation changes only exact structured identifiers; source facts and prose remain unchanged. Full provenance remains local. The 120,000-character guard applies to the serialized compact request.

Chapter, thread, and beat identifiers also use reversible request-local aliases, retaining
their type prefixes for schema compatibility. Role/development dictionary keys are translated
without rewriting their prose. Before requesting index summaries, a lower-bound request
retaining the fixed context, fact identifiers and budgets is measured; if it already exceeds
the guard, generation stops without spending requests on ineffective summaries. Successful
planning and digest caches keep their existing identity because stored semantics are unchanged.

When an actual chapter request exceeds that guard, bounded groups of same-chapter planning indexes are summarized into smaller indexes. Original fact IDs are retained deterministically, original facts are reread during card generation, and summary calls have durable retry records. No fact or chapter is cut to meet the limit. If even the minimum required context exceeds the limit, the task stops explicitly rather than truncating source ownership; index summarization cannot guarantee arbitrary input size or semantic quality.

Clean-test gate for this change: preserve source analysis, candidate reviews and promoted facts, but clear the target task's adventure structure, artifacts, runtime outputs and both sets of generation caches. Regenerate structure first, review global-reference classification, then generate artifacts and inspect runtime use. Offline sizing of an old index is diagnostic evidence only, not a fresh upstream smoke test.
