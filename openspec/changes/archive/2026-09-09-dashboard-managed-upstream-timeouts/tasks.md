# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260909_040000_dashboard_timeout_settings`: seven nullable `FLOAT` columns on `dashboard_settings` (SQLite batch + PostgreSQL), downgrade drops them.
- [x] 1.2 ORM columns, `DashboardSettingsData` effective fields, `DashboardSettingsUpdateData` tri-state fields, repository `update()` value/clear pairs, `_ENVIRONMENT_INHERITABLE_SETTINGS` extended so `provenance` covers the seven settings.
- [x] 1.3 `DashboardSettingsResponse` / `DashboardSettingsUpdateRequest` fields; `PUT` evaluates `find_timeout_invariant_violations` on the effective values before/after and rejects introduced violations with `timeout_invariant_violation`; three `upstream-connect-within-*-budget` rules.
- [x] 1.4 `app/core/config/dashboard_overrides.py` + `DashboardOverridesMiddleware`; proxy service `get_settings()` facade applies the overlay; direct `get_settings()` timeout consumers switched; warmup / automation schedulers bind the snapshot; bridge advertise wait uses `effective_settings`.
- [x] 1.5 `MIGRATING` entries removed; `# T3 → dashboard (deprecated env alias, remove next minor)` on the seven `Settings` fields; startup WARN `warn_environment_shadowed_by_dashboard`.
- [x] 1.6 Tests: overlay unit tests (identity outside a binding, dashboard over env, memoisation, middleware binding and fallback, WARN gating), resolver float states, API round-trip default → dashboard → env with null clear, invariant rejection on effective values, keepalive injector honours a dashboard `0.01 s` over the `10 s` environment value.

## 2. Dashboard

- [x] 2.1 Response/update schema fields (optional with code defaults for older backends; tri-state nullable on update).
- [x] 2.2 `UpstreamTimeoutSettings` card in the Advanced group with `InheritBadge` per field, effective value placeholder, backend-mirroring validation; page wiring and tests.
- [x] 2.3 `settings.upstreamTimeouts.*` strings in `en`, `ko`, `zh-CN` (key-parity test).
- [x] 2.4 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 `make lint` (incl. `check_settings_tiers`), `uv run ty check`, `make migration-check`, unit + integration tests, frontend lint/typecheck/vitest, `openspec validate --specs --strict`, `openspec validate dashboard-managed-upstream-timeouts --strict`, `docs/reference/settings.md` regenerated.
