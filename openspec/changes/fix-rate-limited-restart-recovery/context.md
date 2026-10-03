# Context

## Why the two statuses behaved differently

| | `QUOTA_EXCEEDED` | `RATE_LIMITED` (before this change) |
|---|---|---|
| Debounce source | persisted `blocked_at` + 120 s | in-memory `runtime.cooldown_until` only |
| Survives restart | yes | no — cooldown object is lost |
| Guard clearing persisted `reset_at` | runs once the debounce elapsed and a post-block snapshot exists | never ran after a restart, so the stale `reset_at` kept the account blocked |

`RuntimeState` (including `cooldown_until`) lives in `LoadBalancer._runtime`, which is process-local, while `Account.reset_at` / `Account.blocked_at` are persisted. That asymmetry is the entire bug.

## Concrete failure example

1. 10:00 — account hits the 5-hour window limit. Upstream returns 429 with `resets_at` pointing at the **weekly** reset (3 days away).
2. `handle_rate_limit()` sets `status=RATE_LIMITED`, `blocked_at=10:00`, `reset_at=+3 days`; the account row is persisted.
3. 12:00 — the 5-hour window resets; the usage API reports `used_percent = 3` for the primary window.
4. The process is restarted (deploy) somewhere between 10:00 and 12:00.
5. Before this change: `cooldown_ready` was `False` (no in-memory cooldown), so `effective_runtime_reset` stayed at the persisted `+3 days` value and `apply_usage_quota()` kept `RATE_LIMITED` → the account stayed unusable for 3 days despite being free.
6. After this change: `blocked_at + 120 s` has elapsed and the 12:00 snapshot was recorded after `blocked_at`, so the guard clears the runtime reset → status becomes `ACTIVE` and `reset_at` is cleared on the next persist.

## Why a debounce is still required

Clearing the guard immediately after a restart would let a flapping account back into the pool before any fresh evidence arrives. The 120 s floor matches the existing `QUOTA_EXCEEDED` debounce and only unlocks the *guard*, not the decision itself — recovery still requires a usage snapshot recorded after `blocked_at` whose window value is below 100%.

## Why the dashboard kept showing "Rate limited"

Account status is recomputed inside the proxy path (`_build_states()` → `_persist_selection_state()`), i.e. **only when a request flows through the balancer**. The periodic usage refresh (`UsageRefreshScheduler`, every 60 s) wrote usage rows but never recalculated statuses. An idle account therefore stayed `RATE_LIMITED` in the DB — and on the dashboard — even though its window had reset; only real traffic (or the manual `Reactivate` button) cleared it.

`reconcile_blocked_account_statuses()` closes that gap: after each refresh cycle it rebuilds the state for blocked accounts with the freshly read usage snapshots, persists only real changes, and uses `update_status_if_current()` so a concurrent proxy update wins. Because it reuses `_state_from_account()`, the debounce + freshness rules (including the restart fix above) stay in one place.

Residual detail: the reconciliation runs outside a `LoadBalancer` instance, so it cannot clear that instance's in-memory `runtime.cooldown_until`. An account recovered this way is `ACTIVE` in the DB immediately, while the live balancer may still skip it until the (≤ 300 s) in-process cooldown expires; both converge without intervention.

## Verification notes

- `tests/unit/test_load_balancer.py::test_state_from_account_clears_rate_limited_after_restart_with_persisted_blocked_at` — reproduces the reported symptom (failed before the fix, passes after).
- `tests/unit/test_load_balancer.py::test_state_from_account_keeps_rate_limited_after_restart_when_usage_still_exhausted` — proves the guard is not a blanket unblock: an exhausted window escalates to `QUOTA_EXCEEDED` with the reset marker and stays out of rotation.
- `tests/unit/test_load_balancer.py::test_reconcile_blocked_account_statuses_recovers_after_quota_reset` / `..._skips_healthy_accounts` — the refresh-time reconciliation clears a rate-limited account while leaving a still-exhausted account and healthy accounts untouched.
- `tests/unit/test_usage_updater.py::test_usage_refresh_loop_recovers_blocked_accounts` — one scheduler cycle calls the reconciliation after the usage refresh.
- Existing in-process tests (`test_select_account_recovers_rate_limited_without_reset_after_cooldown`, `..._keeps_rate_limited_with_reset_in_future`, `..._recovers_stale_rate_limited_after_grace_period`) remain green, so the pre-restart behaviour is unchanged.
