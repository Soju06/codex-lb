# Tasks

## 1. Backend

- [x] 1.1 Quota planner tick: one `SettingsCache` snapshot per tick before the session; `_build_states` receives `routing_tunables` and `soft_drain_enabled` resolved from it.
- [x] 1.2 Quota planner forecast endpoint: one snapshot per request, same two arguments.
- [x] 1.3 Usage-refresh recovery: `reconcile_recoverable_account_statuses(dashboard_settings=...)` receives the row the cycle already read; `background_recovery_state_from_account` forwards `routing_tunables` / `soft_drain_enabled` to `_state_from_account`.
- [x] 1.4 `_build_states` / `_state_from_account` comments no longer document the background env fallback.

## 2. Verification

- [x] 2.1 Unit: `background_recovery_state_from_account` health tier follows the passed dashboard value (above-threshold account stays healthy with soft drain off) and forwards the tunables; `reconcile_recoverable_account_statuses` resolves both from a fake dashboard row and falls back to the environment without one.
- [x] 2.2 Integration: after `PUT /api/settings` stores values that differ from the environment, the forecast endpoint and the scheduler tick build states with the dashboard soft-drain value and in-flight penalty.
- [x] 2.3 `make lint`, `uv run ty check`, touched test files, `openspec validate background-paths-read-dashboard-snapshot --strict`, `openspec validate --specs --strict`.
