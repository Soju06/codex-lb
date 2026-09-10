## Why

A Desktop quota poll waits for every eligible account refresh sequentially. Slow upstream retries can make the poll wait for the sum of all account budgets.

## What Changes

Bound the aggregate pool-refresh wait to five seconds. On expiry, read persisted observations and apply the existing complete/fresh projection rules. Caller cancellation still propagates.

## Capabilities

### Modified Capabilities

- `desktop-pooled-usage`: bounded refresh wait with strict persisted-observation fallback.

## Impact

Desktop usage service and route regression tests. No settings, schema, authentication or quota-calculation changes.
