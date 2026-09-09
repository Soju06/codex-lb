# Tasks

## 1. Backend

- [x] 1.1 Alembic revision adding the five nullable `dashboard_settings` columns; ORM, repository seed (NULL) and tri-state update parameters.
- [x] 1.2 `SettingsService` resolves the five through `resolve_inheritable`; `DashboardSettingsData`, `Response` and `UpdateRequest` carry them with provenance; API maps them and audits layer moves.
- [x] 1.3 `RoutingTunables` + `resolve_routing_tunables`; the proxy service passes `effective_routing_tunables(settings)` into `select_account`, `check_opportunistic_admission` and `acquire_account_lease`; the balancer threads it through `_prepare_sticky_selection_states`, `_build_states`, `_state_from_account`, stale-lease reclaim and the detached runtime snapshot; `OverloadIsolationPolicy` and `ErrorRateWeightingPolicy` lose their `from_settings` readers.
- [x] 1.4 `MIGRATING` entries removed; env fields annotated `T3 → dashboard (deprecated env alias, remove next minor)`.

## 2. Dashboard

- [x] 2.1 Schema fields, "Routing weights and overload isolation" section with `InheritBadge` per field and a switch for the error-rate weighting; strings in `en`, `ko`, `zh-CN`.
- [x] 2.2 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver states (NULL → env, NULL + env unset → default, value including 0/false → dashboard), snapshot penalty reaches `_state_from_account`, overload funnel uses the balancer's most recent snapshot.
- [x] 3.2 Integration: `PUT /api/settings` round trip (default → dashboard → omitted unchanged → null → env), out-of-bounds rejection, and the isolation window applied by the balancer following a dashboard `PUT` without restart.
- [x] 3.3 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, `openspec validate dashboard-managed-routing-overload --strict`, `docs/reference/settings.md` regenerated.
