# Maintenance: New-project boundary and display materials

Date: 2026-08-30
Status: accepted for implementation

## Product boundary

The next test loop starts from a fresh workspace. New-project behavior is the source of truth; historical workspaces are not migrated or repaired to fit it. The old incremental-card ("补卡") path is deleted because it creates a second, uncoordinated generation workflow.

## Display materials

Maps, letters, photographs, newspapers, logs, and similar player-facing source assets are represented as independent display-material records. A record keeps its source file and one or more page ranges, a title, GM display notes, and confirmed scene/beat associations. It does not contain an LLM rewrite, a stored crop, or an in-app crop editor. The GM uses an external PDF/image tool when a cropped handout is needed.

Recognition is conservative and reviewable. A candidate becomes a display material only through a fixed review action. Confirmed display materials never enter location coverage or artifact-generation scope.

The current implementation stores these records in `ExampleBundle.display_materials`. A
manual confirmation from an explicitly classified source fact creates the record; the
material editor can then attach it to selected plan/beat pairs. Editing a locked scene
plan itself remains unavailable, and deleting that plan removes its material links.

## Coverage and rebuild

Coverage is an inline warning over the current selected artifacts. It names uncovered source items and their pages. Correcting the scope means creating or re-running a preparation task with an explicit page range; it never creates a supplemental job or appends an unknown card set.

## Facts graph and runtime

The graph is a read-only provenance/navigation enhancement. It contains typed nodes and evidence-backed relationships, opens on a local focus, expands progressively, and always has a filtered list/details fallback. Runtime shows only confirmed relationships and current-scene display-material references. It remains non-linear and does not turn page order into a route.

## Routing and verification

This maintenance follows `domain-modeling` and `codebase-design` for the vocabulary and seams, `diagnosing-bugs` for any observed failure, `tdd` for public contracts, `playwright` for the browser acceptance path, and `code-review` from a fixed commit. No legacy compatibility feature is added to satisfy old fixtures.
