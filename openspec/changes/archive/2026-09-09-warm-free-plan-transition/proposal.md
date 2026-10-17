## Why

A confirmed paid-to-Free plan change can replace the account's prior paid quota
window with a newly available monthly window. The existing same-window safety
guard correctly rejects arbitrary cross-window comparisons, but it also skips
the opted-in warm-up for this confirmed plan transition.

## What Changes

- Preserve the selected account's plan type across one background refresh.
- Treat a confirmed paid-to-Free transition that writes a fresh available
  monthly sample as a long-window warm-up candidate.
- Keep ordinary reset detection restricted to matching canonical windows and
  keep single, unconfirmed Free observations ineligible.
- Add regressions for the consumer-visible warm-up attempt and the safety
  boundaries around unchanged plans and stale monthly history.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Allow an opted-in long-window warm-up after a
  confirmed paid-to-Free transition opens a fresh monthly quota window.

## Impact

- Affected code: `app/core/usage/refresh_scheduler.py` and
  `app/modules/limit_warmup/service.py`.
- Affected tests: focused scheduler and limit warm-up tests.
- No API, schema, migration, setting, dependency, dashboard, or deployment
  change.
