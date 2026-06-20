# Proposal: fix-reports-unknown-useragent-bucket

## Why

The reports user-agent distribution currently drops traffic whose normalized `request_logs.useragent_group` is `null`. That hides unknown-client usage from the `Distribution by UserAgent` card and leaves operators without a stable way to inspect or visually identify those rows.

## What Changes

- Aggregate `request_logs.useragent_group = null` into an `Unknown` bucket in `GET /api/reports` `byUseragent` results.
- Treat `useragent_group=Unknown` on `GET /api/reports` as a filter for those null-backed rows.
- Render the `Unknown` user-agent distribution bucket with a fixed gray legend dot and slice color.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: reports user-agent distribution and filtering now preserve unknown user-agent traffic as an explicit `Unknown` bucket.

## Impact

- Backend: `app/modules/reports/repository.py` user-agent aggregation and filtering.
- Frontend: `frontend/src/features/reports/components/useragent-distribution-donut.tsx` bucket color handling.
- Tests: focused reports repository, reports API, and user-agent donut coverage.
