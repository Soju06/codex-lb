# Fix: Rate-limited accounts recover after quota reset (restart-safe)

## Problem

Accounts can stay stuck in `RATE_LIMITED` after their quota window resets, so the dashboard and the balancer keep excluding them even though the upstream quota is available again.

Root cause is in the runtime-state reconstruction (`_state_from_account` in `app/modules/proxy/load_balancer.py`):

- `RATE_LIMITED` recovery was gated on the **in-memory** `runtime.cooldown_until`. That cooldown is not persisted, so after a process restart `cooldown_ready` stayed `False`.
- Because `cooldown_ready` was `False`, the guard that clears the persisted `reset_at` (`effective_runtime_reset = None`) never ran.
- `apply_usage_quota()` therefore kept `status=RATE_LIMITED` with `reset_at` = the **stale** value stored in the DB (often the weekly window), even when a fresh usage snapshot proved the 5-hour window had already reset.

Result: the account only recovered when the stale `reset_at` timestamp finally passed (or when someone reactivated it manually) — exactly the "vẫn báo Rate limited dù quota đã reset" symptom.

`QUOTA_EXCEEDED` already handled this case correctly (it uses the persisted `blocked_at` marker plus a debounce), which is why the two statuses behaved differently after a restart.

## Solution

### Part 1 — Restart-safe `RATE_LIMITED` recovery

Give `RATE_LIMITED` the same restart-safe debounce that `QUOTA_EXCEEDED` already uses, while keeping the freshness proof that prevents premature recovery:

1. Add `RATE_LIMITED_COOLDOWN_SECONDS = 120.0` (mirrors `QUOTA_EXCEEDED_COOLDOWN_SECONDS`).
2. In `_state_from_account()`, treat the cooldown as ready for `RATE_LIMITED` when **either**
   - the in-memory `runtime.cooldown_until` has expired (unchanged), **or**
   - the persisted `blocked_at` is older than `RATE_LIMITED_COOLDOWN_SECONDS` (new, restart-safe).
3. The existing freshness gate is untouched: the persisted `reset_at` is only cleared when a usage snapshot was **recorded after `blocked_at`** and the window used for the decision (`primary_entry` for `RATE_LIMITED`) shows `used_percent < 100`.

Safety: recovery still requires fresh evidence from the usage API. A restart alone, or a snapshot that still reports an exhausted window, never flips the account to `ACTIVE`.

### Part 2 — Status reconciliation on the periodic usage refresh

The DB status was only recomputed when a request flowed through the balancer, so an idle account kept showing `Rate limited` on the dashboard after its quota reset (only `Reactivate` or traffic cleared it).

`UsageRefreshScheduler._refresh_once()` now re-reads the primary + secondary usage snapshots after the refresh and calls the new `reconcile_blocked_account_statuses()` helper. That helper:

- only considers accounts in `RATE_LIMITED` / `QUOTA_EXCEEDED`,
- rebuilds their state through the same `_state_from_account()` logic (single source of truth for the debounce + freshness rules),
- persists the change through `update_status_if_current()` (optimistic guard on status/`reset_at`/`blocked_at`) so it never clobbers a concurrent proxy update,
- logs `Recovered N blocked account(s) after usage refresh` when something changed.

## Changes

- `app/core/balancer/logic.py`: add `RATE_LIMITED_COOLDOWN_SECONDS`.
- `app/core/balancer/__init__.py`: export it.
- `app/modules/proxy/load_balancer.py`: use the persisted `blocked_at` fallback for `RATE_LIMITED` in the `cooldown_ready` computation; add `reconcile_blocked_account_statuses()`.
- `app/core/usage/refresh_scheduler.py`: re-read usage after refresh and run the reconciliation step.
- `tests/unit/test_load_balancer.py`: regression tests for the restart path and for the reconciliation helper.
- `tests/unit/test_usage_updater.py`: scheduler test proving one refresh cycle triggers the reconciliation.

## Non-goals

- No change to in-process behaviour while the runtime cooldown is active.
- No change to `QUOTA_EXCEEDED` handling.
- No change to the persisted schema or the dashboard API.
