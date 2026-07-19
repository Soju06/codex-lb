## Why

A terminal request-log listing failure currently keeps an otherwise healthy Dashboard behind its page-wide loading skeleton. Operators lose usable overview, quota, and account controls during a request-log outage, when those healthy surfaces are especially valuable.

## What Changes

- Gate overview-backed Dashboard content only on the overview query instead of requiring request logs too.
- Give the Request Logs section independent initial-loading, terminal-error, and ready states.
- Expose a Request Logs-scoped Retry action that refetches only request logs and preserves healthy overview content throughout recovery.
- Add App-route regression coverage for initial partial failure and logs-only recovery.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`: Require independent Dashboard overview and Request Logs loading/failure boundaries.

## Impact

- Dashboard composition in `frontend/src/features/dashboard/components/dashboard-page.tsx`.
- MSW-backed route coverage in `frontend/src/__integration__/dashboard-flow.test.tsx`.
- No backend API, query key, setting, navigation, dependency, or migration changes.
