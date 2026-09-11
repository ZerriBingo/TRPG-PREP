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

The single-chapter beat schema requires rebuilding old adventure structures. For the authorized clean test, back up the database and reset only the selected task's downstream structures, cards, plans, runtime state, and generation caches; preserve promoted facts and upstream review data. Do not reinterpret old multi-chapter beats as valid new structures.

## Chapter Planning Inputs

Artifact local digests are owned by an approved chapter and read only its applicable facts. A fact shared by chapters can be read in each chapter, but a digest is never reused by trimming its IDs while retaining unrelated prose. This may require more local requests than whole-workspace batching.

Reviewed global reference facts remain in the workspace and runtime reference surface. They are not appended to the first chapter or assigned a second time by artifact planning. Actionable multi-chapter material belongs to the applicable chapters during structure review; reference classification must not be used to satisfy an input budget. Chapter and thread summaries supply narrative context without authorizing new citations.

Planning requests encode fact IDs as request-local short identifiers and decode responses before validation/storage. The translation changes only exact structured identifiers; source facts and prose remain unchanged. Full provenance remains local. The 120,000-character guard applies to the serialized compact request.

When an actual chapter request exceeds that guard, bounded groups of same-chapter planning indexes are summarized into smaller indexes. Original fact IDs are retained deterministically, original facts are reread during card generation, and summary calls have durable retry records. No fact or chapter is cut to meet the limit. If even the minimum required context exceeds the limit, the task stops explicitly rather than truncating source ownership; index summarization cannot guarantee arbitrary input size or semantic quality.

Clean-test gate for this change: preserve source analysis, candidate reviews and promoted facts, but clear the target task's adventure structure, artifacts, runtime outputs and both sets of generation caches. Regenerate structure first, review global-reference classification, then generate artifacts and inspect runtime use. Offline sizing of an old index is diagnostic evidence only, not a fresh upstream smoke test.
