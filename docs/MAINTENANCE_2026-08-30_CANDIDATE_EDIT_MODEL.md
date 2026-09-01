# Maintenance: candidate edit model

Date: 2026-08-30
Status: accepted for implementation

## Decisions

The candidate queue is a current-record workflow. A GM edit replaces the
candidate's current text, kind, source references, links, and open questions.
The system does not retain an older model正文 or create a parallel candidate
version. A candidate edit returns to `needs_review` so the replacement is
explicitly reviewed again.

Candidate review history stores operation metadata only: action, resulting
review state, explanation, changed source/field labels, related candidate IDs,
and timestamp. It never stores old text, old field values, or copied model
content. The current candidate fields remain the only editable content.

Split and merge are bounded review operations. Splitting removes the selected
candidate and inserts only the submitted child candidates. Merging removes the
selected candidates and inserts only the submitted merged candidate. Both
operations preserve valid source references, record relation metadata on the
new candidates, and return them to `needs_review`.

An edited claim that is not directly supported by its source must be promoted
as `inference` or `gm_authored`; it must not be presented as an `source_fact`.
Promotion copies the candidate's current content into an independent fact.
Later candidate edits, splits, or merges cannot mutate that fact.

## Public seams

- `PATCH /api/domain/shadow/candidates/{candidate_id}` edits one current
  candidate.
- `POST /api/domain/shadow/candidates/{candidate_id}/split` replaces one
  candidate with submitted children.
- `POST /api/domain/shadow/candidates/merge` replaces two or more candidates
  from one preparation task with one submitted candidate.

All replacement operations are atomic at the storage seam and validate source
references against the owning shadow task before writing.
