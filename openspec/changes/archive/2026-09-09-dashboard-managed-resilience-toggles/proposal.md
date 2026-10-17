# Change: dashboard-managed-resilience-toggles

## Why

The three resilience switches — `soft_drain_enabled` (accounts near their quota move into a draining health tier and are probed back), `deterministic_failover_enabled` (the classified upstream failure decides between retrying on the next account and surfacing the error) and `circuit_breaker_enabled` (per-account breaker on repeated upstream server errors) — are behaviour toggles an operator may need to flip during an incident, yet they exist only as `CODEX_LB_*` environment variables, so changing one means a rollout and a restart of every replica. `configuration-tiers` classifies them T3 ("the dashboard is the management surface") and lists them in `MIGRATING`. Their consumers also read `get_settings().<toggle>` directly from inside selection and retry loops, contrary to the "consumers read the dashboard snapshot" rule the admission caps already follow.

## What Changes

- Three nullable `dashboard_settings` BOOLEAN columns of the same names (alembic `20260909_030000_dashboard_resilience_toggle_settings`). NULL means "inherit": the environment variable, then the code default, keep applying. Nothing is seeded from the environment (`configuration-tiers` P-D). Default behaviour is unchanged.
- `GET`/`PUT /api/settings` expose `softDrainEnabled`, `deterministicFailoverEnabled`, `circuitBreakerEnabled` (effective values) plus `provenance.<name>` entries through the existing `resolve_inheritable` resolver, and accept the same tri-state update as the caps (omitted = unchanged, `null` = back to inherited, boolean = dashboard value). The `CODEX_LB_*` fields stay for one release as deprecated aliases (`# T3 → dashboard (deprecated env alias, remove next minor)`) and leave `MIGRATING`.
- Consumers switch from `get_settings().<toggle>` to the dashboard snapshot their request path already holds, resolved once per request through a shared `resolve_resilience_toggles(snapshot)` helper: account selection (`LoadBalancer.select_account(dashboard_settings=…)` → health-tier evaluation and the "all breakers open" degraded gate), Force Probe settlement (one snapshot before the runtime lock), and the stream / compact / WebSocket failover decisions. No database read or cache await happens inside a runtime lock or a retry loop.
- The upstream client is reached from many request paths without a settings argument, so the request path binds the resolved toggles to its task (`bind_resilience_toggles`, same mechanism as the per-request timeout overrides), rebinds them before each streaming upstream attempt (a capacity keepalive can hand the generator to another task; ContextVars follow tasks), and the client gates breaker *use* on them. Breaker objects are constructed unconditionally (`get_circuit_breaker_for_account(account_id)` no longer takes the toggle); flipping the dashboard toggle therefore takes effect on the next request without a restart, and a task that never bound toggles falls back to the environment layer, which is the pre-change behaviour.
- Dashboard: a "Resilience" switch group under Settings → Advanced with the shared `InheritBadge` ("Inherited from environment (on)", "Default (off)", "Reset to inherited"), strings in `en`, `ko`, `zh-CN`. Unrelated saves never send the toggles, so an inherited toggle stays inherited.
- `docs/reference/settings.md` marks T3 settings that have a dashboard column as `T3 (dashboard)`; `docs/configuration.md` points operators at the new group.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: ADDED requirement — soft drain, the deterministic failover decision and the circuit-breaker selection gate follow the dashboard value (env, then default, when unset), resolved from the selection's dashboard snapshot outside runtime locks.
- `outbound-http-clients`: ADDED requirement — the per-account circuit breaker is constructed unconditionally and consulted only when the request's dashboard toggle is on, so the toggle takes effect without a restart.
- `deployment-installation`: MODIFIED requirement "Removed tunables are fixed constants or derived values" — the two failover enable switches remain the subsystem's only switches but are now dashboard-managed with the environment variable as deprecated fallback.
- `configuration-tiers`: unchanged (this change realises its `MIGRATING` procedure for three fields).

## Impact

- Schema: three nullable columns on `dashboard_settings` (SQLite and PostgreSQL, guarded add/drop).
- API: additive fields on `GET`/`PUT /api/settings`; older dashboards ignore them, the new dashboard tolerates their absence.
- Code: `app/core/config/inheritable.py` (resolver moved out of `SettingsService`, re-exported), `app/core/resilience/toggles.py` (new), `app/core/resilience/circuit_breaker.py`, `app/core/clients/proxy.py`, `app/modules/proxy/load_balancer.py`, `app/modules/proxy/_service/{streaming/retry,compact,websocket/mixin,codex_control,transcribe,warmup}.py`, `app/modules/proxy/service.py` (selection wrapper, thread goal), settings module (`models`, `repository`, `service`, `schemas`, `api`), `app/core/config/{settings,tiers}.py`, frontend `features/settings` (schemas, `resilience-settings.tsx`, page), three locales.
- Operators: no action. Existing `CODEX_LB_SOFT_DRAIN_ENABLED` / `CODEX_LB_DETERMINISTIC_FAILOVER_ENABLED` / `CODEX_LB_CIRCUIT_BREAKER_ENABLED` keep working until the dashboard value is set; the reference page says so. The env fields are removed in the next minor via `_REMOVED_SETTINGS`.

Part of the slop-removal campaign 0908 (C2-3, after the C2-0 provenance foundation).
