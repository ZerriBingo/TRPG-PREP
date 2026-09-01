# Maintenance: first bookshelf refresh (2026-09-01)

## Symptom

From a fresh workbench with no selected workspace:

1. Upload a PDF and finish analysis.
2. Open candidate review.
3. Accept candidates with the combined review-and-promote action.
4. Click **书架** without a hard refresh.

The promotion request succeeded, but the bookshelf remained empty until a
Ctrl+F5 refresh.

## Diagnosis

The task-owned review endpoint returns a promotions array containing the new
bookshelf workspace ID. The client previously discarded that value when the
page had started without a selected workspace, so the current workspace ID
stayed empty. The shelf view correctly rendered its empty state because no
workspace was selected. Opening an existing workspace directly returned facts
consistently, proving persistence and API reads were healthy.

## Fix

- Added adoptPromotedWorkspace() to consume the first returned workspace ID,
  update the current state, and replace the URL query without a full
  navigation.
- Applied the adoption path to both individual and batch candidate review.
- Batch review refreshes the workspace list and workbench after promotion.
- Kept the refresh request token and final cleanup so an older request cannot
  overwrite newer data or leave the loading flag stuck.
- Kept no-store cache headers as a freshness guard, not as the primary fix.

## Verification

- Ran a real browser flow from /workbench.html?view=review with no workspace
  parameter.
- The review response returned HTTP 200 with a promotions workspace ID.
- The URL changed to include that workspace ID.
- Clicking **书架** immediately rendered the existing 78 facts without Ctrl+F5.
- node --check frontend/workbench.js
- python scripts/test_shelf_refresh_contract.py
- Existing focused review and freshness tests also pass.

## Scope

This maintenance only fixes first-promotion workspace selection and refresh.
It does not restore retired supplemental-card, handout auto-generation, old
project migration, or parallel review queues.
