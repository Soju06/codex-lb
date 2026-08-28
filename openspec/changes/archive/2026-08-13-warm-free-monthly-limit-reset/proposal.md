## Why

Free accounts expose their long quota as a normalized `monthly` window, but
the background limit warm-up path still compares only `secondary` rows. A free
account can therefore move from an exhausted monthly sample into a new monthly
reset without receiving the same opt-in warm-up that paid long-window resets
receive.

## What Changes

- Load monthly usage snapshots alongside primary and secondary snapshots in
  each selected-account scheduler slice.
- Select the plan-applicable long window for warm-up comparison: monthly for
  plans with monthly capacity, otherwise secondary.
- Require the before and after samples to represent the same canonical window
  before treating a `reset_at` jump as a confirmed reset.
- Persist a monthly reset warm-up attempt with `window="monthly"`.
- Add regressions for scheduler query scope, window selection, and the
  consumer-visible warm-up attempt.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Treat a free account's monthly quota as the selected
  long window when evaluating reset-confirmed limit warm-up.

## Impact

- Affected code: `app/core/usage/refresh_scheduler.py` and
  `app/modules/limit_warmup/service.py`.
- Affected tests: focused usage scheduler and limit warm-up unit tests.
- No API, schema, migration, setting, dependency, dashboard, or deployment
  change.
