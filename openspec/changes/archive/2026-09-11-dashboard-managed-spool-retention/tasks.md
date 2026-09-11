# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260910_010000_dashboard_spool_retention`: nullable FLOAT `http_responses_session_bridge_operation_spool_retention_seconds` on `dashboard_settings`; ORM column; repository seeds NULL and applies the tri-state update.
- [x] 1.2 `DashboardSettingsData` / `Response` / `UpdateRequest` carry the effective value and provenance via `resolve_inheritable`; the env field keeps the `# T3 → dashboard (deprecated env alias, remove next minor)` marker; `tiers.MIGRATING` entry removed; `check_settings_tiers` passes.
- [x] 1.3 `app/core/config/spool_retention.py` owns the setting name, the resolver and the floor terms; `_abandoned_bridge_retention_seconds` delegates its four-term reuse window to it so both read one definition.
- [x] 1.4 `PUT /api/settings` rejects an effective retention below the derived floor with `spool_retention_below_floor`, naming the binding term; only violations the change introduces are rejected, and from an already-below state only "no worse" updates (retention no shorter, floor no higher) are accepted. `GET` reports the floor. Startup warns about an already-below configuration on the existing warn-only effective-value pass.
- [x] 1.5 Consumers: the startup purge and the leader-gated retention pass resolve the window from the dashboard snapshot the pass already holds (one resolve per pass, outside per-item loops).

## 2. Dashboard

- [x] 2.1 `httpResponsesSessionBridgeOperationSpoolRetentionSeconds` (+ the floor) on the settings schema, nullable on the update request; factories updated.
- [x] 2.2 The field joins the Data retention card with the shared `InheritBadge`, reset-to-inherited, the privacy-explicit label, the floor hint and the client-side floor check; `settings.retention.spool.*` strings in `en`, `ko`, `zh-CN`.
- [x] 2.3 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver states (NULL→env, NULL+env unset→default, dashboard wins), floor terms at defaults, when a dashboard reuse window is raised, and when a lowered bridge budget leaves the claimed-circuit grace binding; idle TTLs read at call time. Integration: API round trip (default → dashboard → cleared/env → unchanged on omit, column stays NULL), floor accept/reject in both directions, under a lowered bridge budget, and from an already-below-floor state (unrelated edit and improvement accepted; deepening and floor-raising rejected), migration upgrade/downgrade, startup warning.
- [x] 3.2 Consumer: the retention pass cuts at the dashboard window rather than the environment alias.
- [x] 3.3 Frontend: the card saves and rejects around the floor, reset-to-inherited PUTs null, busy disables the input; i18n parity.
- [x] 3.4 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, simplicity budgets, `openspec validate dashboard-managed-spool-retention --strict`, `docs/reference/settings.md` regenerated.
