## Why

The current early-reset mitigation for `quota_exceeded` depends on in-memory runtime markers. That helps while the same process stays alive, but it stops helping after a restart because the balancer no longer knows when the account was blocked.

In practice this means an account can still remain stuck in `quota_exceeded` after an early upstream reset if:

- fresh usage already shows the quota is back
- but the process that originally blocked the account is gone
- and the persisted `reset_at` guard is still in the future

## What Changes

- Persist the quota/rate-limit block marker on the account record.
- Let quota recovery use the persisted block marker when deciding whether fresh usage is newer than the original block event.
- Preserve the debounce behavior that avoids immediate stale-data bouncing after a fresh quota block.
- Clear the persisted block marker when the account returns to `active`.

## Impact

- Early upstream quota resets can recover automatically even after a process restart.
- The system still avoids immediate false recovery from stale pre-block usage rows.
- Manual `reactivate` remains available, but should no longer be required for the normal early-reset case.
