## Why

Accounts that have already recovered in persisted usage data can remain stuck in `rate_limited` or `quota_exceeded` until a later request happens to run request-path selection logic or an operator manually reactivates the account. That leaves the dashboard showing recovered 5h/7d usage alongside a stale blocked status.

Background usage refresh already writes the authoritative usage snapshots that prove recovery. The same scheduler should reconcile recoverable account statuses back to `active` so persisted account state catches up without waiting for live traffic.

## What Changes

- Add a background account-status reconciliation step after each usage refresh cycle.
- Limit reconciliation to accounts currently persisted as `rate_limited` or `quota_exceeded`.
- Recover those accounts to `active` only when fresh persisted usage data proves the blocked window has recovered.
- Clear persisted `reset_at` and `blocked_at` markers when background reconciliation restores an account to `active`.
- Keep reconciliation recovery-only; it MUST NOT tighten `active` accounts into blocked statuses.

## Capabilities

### New Capabilities

### Modified Capabilities
- `usage-refresh-policy`: background usage refresh now reconciles recoverable blocked statuses back to `active` using fresh persisted usage snapshots.

## Impact

- Affects `app/core/usage/refresh_scheduler.py`, `app/modules/proxy/load_balancer.py`, and account repository write paths.
- Affects persisted `accounts.status`, `accounts.reset_at`, and `accounts.blocked_at` maintenance.
- Adds regression coverage for background recovery and stale dashboard/account-list status behavior.
