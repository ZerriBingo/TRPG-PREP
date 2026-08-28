# Skill Audit - 2026-08-28

The repository uses task-intent routing. Third-party skills are reviewed individually and are not copied into the project.

## Adopted

- `setup-matt-pocock-skills`: used to establish repository governance files; global installation was attempted and its PromptScript limitation recorded.
- `codebase-design`: adopted vocabulary for deep modules, interfaces, seams, adapters, locality, and leverage.
- `domain-modeling`: used alongside `CONTEXT.md` and ADRs for domain terminology.
- `grill-with-docs`: applied as document-bound decision review for supplemental cards, coverage, facts graph, and runtime mode.
- `tdd`: adopted for future HTTP/job lifecycle tests at public seams.
- `playwright`: retained for future browser-flow verification; no third-party copy needed.

## Reviewed, Not Installed

- UI mockup/prototype: useful only after state and workflow decisions stabilize; defer until the current cards/runtime flow is testable.
- Migration/data-migration skills: no schema migration is currently required; defer until a persisted model change is specified.
- Generic automation/orchestration skills: rejected because they add process surface without solving the current job lifecycle issue.
- Pre-commit setup: deferred; the repository is Python/vanilla JS without a package-manager test pipeline matching the skill assumptions.

## Routing Rule

Use diagnosing-bugs for failures, grilling for explicit product stress tests, research for external primary-source work, code-review from a fixed commit, and TDD for new public-seam regression coverage.
