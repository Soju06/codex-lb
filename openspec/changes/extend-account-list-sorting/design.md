## Goals

Account sorting by status and remaining quota.

## Decisions

Reuse the existing Accounts sort dropdown and page state rather than introduce the fork subscription fields or a replacement table. Operators can find exhausted monthly accounts or healthy accounts first. Sorting uses reported percentages, does not infer missing values from plan names, and does not change routing or quota accounting.

## Validation

Exercise public UI/API behavior, edge cases, lint/type checks and strict OpenSpec validation. Capture synthetic before/after screenshots from the upstream baseline and this branch.
