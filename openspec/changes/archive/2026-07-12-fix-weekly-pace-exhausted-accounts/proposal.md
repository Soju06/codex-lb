## Why

The dashboard weekly credits pace card currently excludes accounts whose
persisted status is `quota_exceeded` or `rate_limited`. A fully used weekly
account is therefore removed from the pool exactly when its zero remaining
credits should increase the pace gap and recovery warning.

The same card also labels positive `scheduleGapCredits` as "behind schedule".
That is easy to read as slower-than-planned consumption, even though the metric
means actual remaining credits are below scheduled remaining credits and usage
is over the planned burn for this point in the week.

## What Changes

- Count fresh weekly usage rows from `rate_limited` and `quota_exceeded`
  accounts in weekly pace totals and forecasts.
- Continue excluding reauth-required, paused, deactivated, missing, and stale
  accounts from the pace pool.
- Label positive schedule gaps as over planned usage instead of "behind
  schedule".
- Cover a fresh `quota_exceeded` account at 100% weekly usage in the dashboard
  projections integration test.

## Impact

- Modified capability: `frontend-architecture`
- Backend: dashboard weekly pace filtering
- Frontend: weekly credits pace card copy
- Tests: dashboard projections and pace-card coverage
