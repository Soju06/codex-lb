## Why

Codex-LB refreshes account credentials reactively when a request selects an account. Idle accounts can sit unused long enough for refresh credentials to age, forcing operators to reauthenticate accounts that could have been kept alive with a safe proactive refresh.

## What Changes

- Add an Auth Guardian background scheduler that periodically force-refreshes stale active accounts, including accounts not currently routed by traffic.
- Guard the scheduler with existing leader election so multi-replica deployments do not duplicate refresh work.
- Bound each pass by batch size and concurrency, add jitter between loops, and back off per-account after refresh failures.
- Log only account id/safe alias and error metadata; never log token material.

## Impact

- Affects app startup/shutdown, auth refresh scheduling, and settings.
- Adds unit coverage for stale active account selection, forced refresh, leader-election skip, and failure backoff behavior.
