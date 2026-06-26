## Why

The dashboard can show `Response schema mismatch` when `GET /api/dashboard/overview` returns `summary.comparison: null`. The backend emits that explicit null when previous-window comparison is unavailable, but the frontend schema only accepted an omitted comparison block.

## What Changes

- Update the frontend dashboard overview schema to accept `summary.comparison: null`.
- Preserve existing support for older responses that omit `summary.comparison`.
- Add regression coverage for the backend's explicit-null overview shape.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Dashboard overview parsing treats nullable comparison metadata as a valid unavailable-comparison state.

## Impact

- Frontend dashboard response schema in `frontend/src/features/dashboard/schemas.ts`
- Frontend schema regression coverage in `frontend/src/features/dashboard/schemas.test.ts`
