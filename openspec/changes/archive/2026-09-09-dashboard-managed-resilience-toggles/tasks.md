# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260909_030000_dashboard_resilience_toggle_settings`: nullable BOOLEAN `soft_drain_enabled`, `deterministic_failover_enabled`, `circuit_breaker_enabled` on `dashboard_settings`; ORM columns; repository seeds NULL and applies tri-state updates.
- [x] 1.2 `DashboardSettingsData` / `Response` / `UpdateRequest` carry the effective booleans; provenance entries via `resolve_inheritable` (moved to `app/core/config/inheritable.py`, re-exported from the service); audit records ownership-only changes.
- [x] 1.3 `Settings` fields kept as deprecated aliases with the `# T3 → dashboard (deprecated env alias, remove next minor)` marker; `tiers.MIGRATING` entries removed; `check_settings_tiers` passes.
- [x] 1.4 `app/core/resilience/toggles.py`: `resolve_resilience_toggles(snapshot)`, `bind_resilience_toggles` / `current_resilience_toggles` (task-bound for the upstream client).
- [x] 1.5 Consumers: `LoadBalancer.select_account(dashboard_settings=…)` resolves once (soft drain via `_SelectionInputs`, breaker gate), Force Probe settlement takes one snapshot before the lock, stream / compact / WebSocket failover decisions read the bound toggles; `get_circuit_breaker_for_account(account_id)` constructs unconditionally and the client gates use.

## 2. Dashboard

- [x] 2.1 `softDrainEnabled` / `deterministicFailoverEnabled` / `circuitBreakerEnabled` on the settings schema (nullable on the update request); factories updated.
- [x] 2.2 `ResilienceSettings` switch group in Advanced settings with `InheritBadge` per switch; `settings.resilience.*` strings in `en`, `ko`, `zh-CN`.
- [x] 2.3 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver in each state (incl. booleans), service provenance, hot-path resolver and task binding, breaker construction independent of the toggle. Integration: API round trip (default → dashboard → cleared/env → unchanged on omit, column stays NULL), circuit breaker consulted / not consulted after `PUT` without restart, soft-drain health tier follows the dashboard value.
- [x] 3.2 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, simplicity budgets, `openspec validate dashboard-managed-resilience-toggles --strict`, `docs/reference/settings.md` regenerated.
