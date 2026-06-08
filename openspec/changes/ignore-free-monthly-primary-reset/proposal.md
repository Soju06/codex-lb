## Why

Issue #883 reports a new upstream monthly usage window leaking into free-account quota state. codex-lb already hides zero-capacity free-plan primary rows from the UI, but one recovery path still treated any persisted primary window as a real 5h limiter when the account was already marked `rate_limited`.

That let a free account keep a fake primary reset deadline derived from the new monthly snapshot. The dashboard/account list then surfaced the account as blocked and could render `Reset now` even though the normalized free-account quota model is monthly-only.

## What Changes

- Treat zero-capacity primary usage as a status-recovery signal only when it is the canonical 5h primary window.
- Ignore non-5h zero-capacity primary windows (including the new monthly shape) when deriving persisted account status for account summaries and proxy runtime state.
- Add regressions proving stale non-5h primary rows do not keep free accounts `rate_limited` under the new monthly-only quota model.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: zero-capacity free-plan primary windows only affect rate-limit recovery when the window is the canonical 5h primary quota, not arbitrary upstream monthly windows.

## Impact

- Code: `app/core/usage/__init__.py`, `app/modules/accounts/mappers.py`, `app/modules/proxy/load_balancer.py`
- Tests: `tests/integration/test_accounts_api_extended.py`, `tests/unit/test_load_balancer.py`
- API/UI effect: free accounts stop surfacing fake primary reset state from monthly usage snapshots while the surviving quota presentation remains monthly-only.
