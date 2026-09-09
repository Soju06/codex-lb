# Change: dashboard-managed-codex-prewarm

## Why

The Codex HTTP-bridge session prewarm — one `generate=false` warm-up sent on the first turn of a new Codex session so the visible turn starts on a warmed upstream session, at the cost of one extra upstream request per new session — is a behaviour switch an operator turns on or off while watching TTFT and upstream request volume. It existed only as `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED`, so flipping it meant editing the environment on every replica and restarting. `configuration-tiers` classifies it T3 and it sat in the `MIGRATING` backlog. This is M3 of the slop-removal campaign; the provenance surface and the shared resolver landed in `settings-provenance` (#2214), the switch pattern in `dashboard-managed-resilience-toggles` (#2220).

## What Changes

- `dashboard_settings` gains one nullable BOOLEAN column of the same name (alembic `20260909_100000_dashboard_codex_prewarm`). NULL inherits the deprecated environment alias, then the code default (off); the first-boot seed and the migration never copy the environment value into the row. Default behaviour is unchanged.
- `GET`/`PUT /api/settings` expose `httpResponsesSessionBridgeCodexPrewarmEnabled` (effective value) plus a `provenance` entry through `resolve_inheritable`, and accept the same tri-state update as the other inheritable settings (omitted = unchanged, `null` = back to inherited, boolean = dashboard value; `false` is a value). The `CODEX_LB_*` field stays one release as a deprecated alias (`# T3 → dashboard (deprecated env alias, remove next minor)`) and leaves `MIGRATING`.
- The switch joins `DASHBOARD_OVERRIDE_SETTINGS`, so the request entry point's `DashboardOverridesMiddleware` — which already reads the settings-cache snapshot once per request — folds the non-NULL column over the deprecated env alias before `_service_get_settings()` returns. The single consumer, `_http_bridge_prewarm_enabled(settings)`, therefore keeps its one-argument shape and stays a plain memory read on `Settings`. `_maybe_prewarm_http_bridge_session` resolves it **before** taking `prewarm_lock` and adds no settings read — and no `await` — under that lock (issues #1971/#1972 wedged keyed submits on exactly that pattern). The admission and reconnect helpers the lock body already called keep their own pre-existing settings-cache reads; this change adds none.
- Dashboard: a "Session bridge" card under Settings → Advanced (next to Resilience) with one switch, "Codex session prewarm", whose label explains the one-warm-up-per-new-session behaviour and its one-extra-upstream-request cost, plus the shared `InheritBadge`; strings in `en`, `ko`, `zh-CN`. Unrelated saves never send the switch.
- Helm keeps rendering `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` from `config.sessionBridgeCodexPrewarmEnabled` during the alias period (default `false`, matching the code default), so existing charts keep working until the alias is removed.
- `docs/reference/settings.md` shows the switch as `T3 (dashboard)`; `docs/configuration.md` points operators at the card; the `responses-api-compat` context and ops notes name the dashboard setting instead of "the flag".

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: MODIFIED requirement "Removed tunables are fixed constants, derived values, or dashboard settings" — prewarm eligibility stays the single switch alone (no canary percent, no cohort lists), but the switch is a dashboard runtime setting with the environment variable as deprecated alias while the column is NULL, resolved before the prewarm lock; the two prewarm scenarios are restated against the switch and a new scenario fixes the alias/dashboard hand-over without a restart.
- `configuration-tiers`: unchanged (this change realises its `MIGRATING` procedure for one field).

## Impact

- Schema: one nullable column on `dashboard_settings` (SQLite and PostgreSQL, guarded add/drop).
- API: additive field on `GET`/`PUT /api/settings`; older dashboards ignore it, the new dashboard tolerates its absence.
- Code: `app/modules/proxy/_service/http_bridge/{helpers,request_submit}.py`, settings module (`models`, `repository`, `service`, `schemas`, `api`), `app/core/config/{settings,tiers}.py`, frontend `features/settings` (schemas, `session-bridge-settings.tsx`, page), three locales.
- Operators: no action. `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` keeps working until the dashboard value is set; the env field is removed in the next minor via `_REMOVED_SETTINGS`.

Part of the slop-removal campaign 0908 (M3).
