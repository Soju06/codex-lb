# Tasks

## Part 1 — Restart-safe RATE_LIMITED recovery

- [x] T1: Add `RATE_LIMITED_COOLDOWN_SECONDS = 120.0` to `app/core/balancer/logic.py` with a comment explaining the restart-safe debounce
- [x] T2: Export `RATE_LIMITED_COOLDOWN_SECONDS` from `app/core/balancer/__init__.py`
- [x] T3: Import `RATE_LIMITED_COOLDOWN_SECONDS` in `app/modules/proxy/load_balancer.py`
- [x] T4: Extend the `cooldown_ready` computation in `_state_from_account()` so `RATE_LIMITED` also becomes ready when the persisted `blocked_at` is older than the debounce
- [x] T5: Add regression test `test_state_from_account_clears_rate_limited_after_restart_with_persisted_blocked_at` (fresh usage below 100% → `ACTIVE`, `reset_at` cleared)
- [x] T6: Add regression test `test_state_from_account_keeps_rate_limited_after_restart_when_usage_still_exhausted` (fresh usage at 100% → not `ACTIVE`; escalates to `QUOTA_EXCEEDED` with the reset marker)

## Part 2 — Periodic status reconciliation

- [x] T7: Add `reconcile_blocked_account_statuses()` to `app/modules/proxy/load_balancer.py` (only `RATE_LIMITED`/`QUOTA_EXCEEDED`, optimistic persist via `update_status_if_current`)
- [x] T8: Wire it into `UsageRefreshScheduler._refresh_once()` after the usage refresh, using freshly re-read primary + secondary snapshots
- [x] T9: Log recovered accounts (`Recovered N blocked account(s) after usage refresh`) and keep the selection-cache invalidation
- [x] T10: Add `test_reconcile_blocked_account_statuses_recovers_after_quota_reset` + `..._skips_healthy_accounts` to `tests/unit/test_load_balancer.py`
- [x] T11: Add `test_usage_refresh_loop_recovers_blocked_accounts` to `tests/unit/test_usage_updater.py`

## Verification

- [x] T12: `pytest tests/unit/test_load_balancer.py` (64 passed), `pytest tests/unit/test_usage_updater.py` (39 passed)
- [x] T13: `ruff check` + `ruff format --check` + `ty check app` clean
- [x] T14: Full `pytest tests/unit` (1448+ passed, 40 skipped)
- [ ] T15: `openspec validate` for this change, then archive after review
