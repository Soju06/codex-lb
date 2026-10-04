## Why

Reuse the existing Accounts sort dropdown and page state rather than introduce the fork subscription fields or a replacement table. Operators can find exhausted monthly accounts or healthy accounts first. Sorting uses reported percentages, does not infer missing values from plan names, and does not change routing or quota accounting.

## What Changes

- Account sorting by status and remaining quota.
- Include regression coverage and before/after dashboard evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: account sorting by status and remaining quota.

## Impact

Focused dashboard changes; the model-picker change also extends dashboard model metadata. No migrations, new settings, dependencies, navigation items, or setup steps.
