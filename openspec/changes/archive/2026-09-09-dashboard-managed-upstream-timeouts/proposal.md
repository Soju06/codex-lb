# Change: dashboard-managed-upstream-timeouts

## Why

Seven upstream timeouts and request budgets — `upstream_connect_timeout_seconds`, `proxy_request_budget_seconds`, `compact_request_budget_seconds`, `transcription_request_budget_seconds`, `stream_idle_timeout_seconds`, `proxy_downstream_websocket_idle_timeout_seconds` and `sse_keepalive_interval_seconds` — are T3 behaviour tunables (`configuration-tiers`) that an operator may need to change while the proxy is running (a slow upstream, a front-door proxy with a short idle window, long tool-running turns), yet they live only in the process environment: changing one means editing every replica's environment and restarting it. They are the first group of the `MIGRATING` backlog that `configuration-tiers` requires to move to the dashboard, and they consume the provenance surface and the single resolver that `settings-provenance` landed.

## What Changes

- `dashboard_settings` gains seven nullable columns named after the `Settings` fields. NULL inherits the environment value (or the code default); a non-NULL column wins on every replica. The environment is never copied into the column.
- `GET`/`PUT /api/settings` expose the seven effective values as top-level fields plus a `provenance` entry each (`dashboard` | `env` | `default`, with `env_value` and `default`), using the existing tri-state PUT contract (omitted = unchanged, `null` = clear to inherit, value = store).
- `PUT /api/settings` evaluates the startup timeout invariants (`app/core/timeout_invariants.py`) against the **effective** values before and after the change and rejects, with `400 timeout_invariant_violation`, a change that introduces a violation (for example a proxy budget below the admission wait, or a connect timeout above a budget). Three new rules bound the connect timeout by the proxy, compact and transcription budgets; a pre-existing environment violation does not block unrelated edits.
- Consumers read the dashboard value through the `SettingsCache` snapshot bound once per request or WebSocket connection (`DashboardOverridesMiddleware` → `with_dashboard_overrides(get_settings())`, applied by the proxy service settings facade); the warmup and automation schedulers bind the same snapshot for their runs. The database is never read in a hot loop. The seven `Settings` fields stay as deprecated env aliases (`# T3 → dashboard (deprecated env alias, remove next minor)`), startup evaluates the timeout invariants on the same effective values (after the settings row is readable) and emits one WARN naming any env alias that is set while the dashboard owns the value.
- Dashboard: an "Upstream timeouts" card in Settings → Advanced with one input per field, the `InheritBadge` (environment / default / reset to inherited), the effective value as placeholder, and validation that mirrors the backend (positive values, keepalive `0` allowed, connect within each budget). Strings in `en`, `ko`, `zh-CN`.
- `app/core/config/tiers.py`: the seven `MIGRATING` entries are removed (the columns exist). No new `Settings` field; no default changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: MODIFIED "Long Codex websocket turns tolerate extended upstream silence" (the compact budget, stream idle timeout and proxy request budget are dashboard-managed with fixed precedence), MODIFIED "HTTP bridge streams emit downstream liveness frames while pending" (the keepalive interval comes from the dashboard snapshot), MODIFIED "Responses upstream websocket liveness is bounded" (the downstream WebSocket idle timeout comes from the dashboard snapshot).
- `outbound-http-clients`: ADDED "Upstream connect timeout is dashboard-managed".
- `audio-transcriptions-compat`: MODIFIED "Transcription proxy requests use a bounded retry budget" (the budget is dashboard-managed).
- `configuration-tiers`: no delta — the provenance shape and precedence are already specified; this change applies them to seven more settings.

## Impact

- Schema: alembic `20260909_040000_dashboard_timeout_settings` (seven nullable `FLOAT` columns; downgrade drops them).
- Code: `app/core/config/dashboard_overrides.py` (registry, request-scoped overlay), `app/core/middleware/dashboard_overrides.py`, `app/modules/settings/{service,schemas,api,repository}.py`, `app/modules/proxy/service.py` (settings facade), the direct `get_settings()` timeout consumers in `app/core/clients/{proxy,proxy_websocket,files}.py`, `app/modules/proxy/{api,load_balancer,http_bridge_forwarding}.py`, `app/modules/proxy/_service/{warmup,realtime_live}.py`, `app/modules/model_sources/forwarding.py`, `app/modules/automations/{service,repository}.py`, `app/main.py`; `app/core/timeout_invariants.py` (three connect-within-budget rules).
- API: additive fields on `GET`/`PUT /api/settings`; a new `400` code `timeout_invariant_violation`.
- Operators: the seven `CODEX_LB_*` variables keep working as fallbacks for one release; the dashboard value wins once set.

Part of the slop-removal campaign 0908 (C2-1; C2-2 routing/overload and C2-3 resilience toggles follow the same pattern).
