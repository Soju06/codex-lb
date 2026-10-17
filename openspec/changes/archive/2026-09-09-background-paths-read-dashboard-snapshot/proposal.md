# Change: background-paths-read-dashboard-snapshot

## Why

`dashboard-managed-resilience-toggles` (#2220) and `dashboard-managed-routing-overload` (#2224) moved soft drain and the five routing/overload knobs into `dashboard_settings`, and every request path resolves them from the `SettingsCache` snapshot it already holds. Both changes deferred the background state builds: the quota planner tick (`QuotaPlannerScheduler._run_once_as_leader`), the quota planner forecast endpoint (`GET /api/quota-planner/forecast`) and the usage-refresh recovery reconciliation (`reconcile_recoverable_account_statuses` → `background_recovery_state_from_account`) passed no snapshot into `_build_states` / `_state_from_account`, so the health tier they computed inherited `CODEX_LB_SOFT_DRAIN_ENABLED` and the pressure knobs came from the environment. The configuration-tiers policy (P-F / P-F') is that every consumer of a dashboard-managed T3 setting reads the dashboard snapshot; an operator turning soft drain off in the dashboard must not see planner states that still drain. This is the follow-up both PRs recorded.

## What Changes

- The quota planner tick takes one dashboard-settings snapshot per tick (`await get_settings_cache().get()`, before its session, outside any runtime lock) and the forecast endpoint takes one per request (before its first repository query, so a cache refresh never opens a second session while the request session holds a pooled connection); both pass `effective_routing_tunables(snapshot)` and `resolve_resilience_toggles(snapshot).soft_drain_enabled` into `_build_states`.
- `reconcile_recoverable_account_statuses` accepts the `dashboard_settings` row the usage-refresh cycle already read for the warmup service, resolves the toggle and the tunables once and passes them into `background_recovery_state_from_account`, which forwards them to `_state_from_account`. Callers without a row (tests) keep the environment layer.
- No new setting, column, endpoint or dashboard surface; no runtime lock reads settings.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: the requirements "Resilience toggles follow the dashboard value" and "Routing weights and overload isolation are dashboard settings" (both added by the two unarchived sibling changes above; this change builds on their text) no longer allow the background state builds to resolve the environment layer — they take one snapshot per tick or request — and gain a scenario each. This change MUST be archived after `dashboard-managed-resilience-toggles` and `dashboard-managed-routing-overload`.

## Impact

- Code: `app/modules/quota_planner/scheduler.py`, `app/modules/quota_planner/api.py`, `app/core/usage/refresh_scheduler.py`, `app/modules/proxy/load_balancer.py` (`background_recovery_state_from_account` signature; comments).
- Behaviour: the planner's simulated account states and the recovery state build follow the dashboard soft-drain toggle and routing tunables within the settings cache TTL. Neither path routes traffic on the health tier, so account selection is unchanged.
- No migration, no API change, no new environment variable.
