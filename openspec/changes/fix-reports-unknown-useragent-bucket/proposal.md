# Proposal: fix-reports-unknown-useragent-bucket

## Why

The reports user-agent distribution currently collides real normalized `request_logs.useragent_group = "Unknown"` traffic with rows whose normalized `request_logs.useragent_group` is `null`. That merges distinct populations in the `Distribution by UserAgent` card and breaks drill-down totals because the `useragent_group=Unknown` filter currently resolves only to null-backed rows.

## What Changes

- Aggregate `request_logs.useragent_group = null` into a distinct `Missing User-Agent` bucket in `GET /api/reports` `byUseragent` results.
- Preserve real `request_logs.useragent_group = "Unknown"` traffic as its own `Unknown` bucket and filter target.
- Render the `Missing User-Agent` user-agent distribution bucket with a fixed gray legend dot and slice color.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: reports user-agent distribution and filtering now preserve unknown user-agent traffic as an explicit `Unknown` bucket.

## Impact

- Backend: `app/modules/reports/repository.py` user-agent aggregation and filtering.
- Frontend: `frontend/src/features/reports/components/useragent-distribution-donut.tsx` bucket color handling.
- Tests: focused reports repository, reports API, and user-agent donut coverage.
