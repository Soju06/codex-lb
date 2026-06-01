## Why

Operators need explicit routing modes for maintenance and quota burn-down:
use smaller-capacity accounts before expensive capacity, spend accounts whose
quota resets soon, or pin traffic to one selected account during diagnostics.

## What Changes

- Add `sequential_drain`, `reset_drain`, and `single_account` routing strategies.
- Persist and expose an optional `single_account_id` dashboard setting.
- Surface the new strategies in dashboard settings and status labels.
- Keep sticky/budget fallback behavior from overriding explicit drain or single-account modes.

## Impact

- Existing routing strategies and defaults remain unchanged.
- Operators can deliberately drain accounts or isolate one account without
  deleting, pausing, or reimporting accounts.
- The dashboard settings table gains one nullable, idempotently migrated column.
