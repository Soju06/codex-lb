## Why

The quota phase planner and the staggered idle limit warm-up hardcode the 5-hour primary window (`FIVE_HOUR_WINDOW_SECONDS`, `_ROLLING_WINDOW_SECONDS = 300 * 60`) and derive window state from the wrong field: `AccountState.reset_at` is only set for blocked statuses, so every healthy account looks permanently "cold" to the planner. With OpenAI's temporary removal of the 5h Codex limit (2026-07-12) and upstream codex's duration-driven window model, both assumptions now misfire:

- Cold/active detection never sees a healthy account's live primary window, so expiring-window bonuses never apply and cold-start costs land uniformly (accidentally inert), and warmup candidacy treats every healthy account as cold — including accounts that no longer have a short window to pre-start.
- Warmup planning subtracts exactly 5 hours from demand peaks and models every planned window as 5 hours, which is wrong for any other server-reported duration.
- The warm-up execution gate and effect observations accept weekly-only or window-less usage samples as if a short window existed.

## What Changes

- Phase planning is scoped to accounts whose latest primary sample reports a short rolling window (duration metadata present and at most 24 hours): warmup candidates, cold-start routing costs, the warm-up execution gate, and warmup-effect observed confidence are all limited to such accounts.
- Cold/active window detection derives from the primary window sample (`AccountState.primary_reset_at`), not the blocked-status `reset_at`; `AccountState` gains a `primary_window_minutes` carrier populated from selection state.
- All phase math (candidate start times, planned resets, pool-simulation spans, synchronization penalties) uses each account's observed window duration; planner actions carry their window duration.
- Routing cost nudges keep their existing mode behavior (the spec requires them enabled by default); fixing cold/active detection activates the intended active-vs-cold differentiation that the wrong field previously masked.
- The staggered idle limit warm-up derives its rolling cycle from the account's observed primary window duration, keeping 300 minutes only as the fallback when duration metadata is missing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quota-phase-planner`: short-window plannability scoping, duration-derived phase math, audit-only shadow routing.
- `usage-refresh-policy`: staggered idle warm-up cycles become duration-neutral.

## Impact

- Code: `app/core/balancer/logic.py`, `app/modules/proxy/load_balancer.py`, `app/modules/quota_planner/logic.py`, `app/modules/quota_planner/warmup.py`, `app/modules/limit_warmup/service.py`
- Tests: `tests/unit/test_quota_planner.py`, `tests/unit/test_limit_warmup.py`, `tests/unit/test_load_balancer.py`
- Specs: `openspec/specs/quota-phase-planner/spec.md`, `openspec/specs/usage-refresh-policy/spec.md`
