# Change: dashboard-managed-routing-overload

## Why

Five balancer knobs were environment-only: the overload isolation window (`CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS`, tuned during the 2026-09-08 overloaded-account incident, #2166), the error-rate weighting kill switch (`CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED`), the in-flight pressure penalty (`CODEX_LB_PROXY_ACCOUNT_INFLIGHT_PENALTY_PCT`), the leased-token weight (`CODEX_LB_PROXY_ACCOUNT_LEASE_TOKEN_WEIGHT`) and the account lease TTL (`CODEX_LB_PROXY_ACCOUNT_LEASE_TTL_SECONDS`). Changing any of them meant editing the environment on every replica and restarting, which is exactly what `configuration-tiers` classifies as a T3 defect: a value an operator changes while the proxy is running belongs in the dashboard, with the fixed precedence code default < environment < dashboard. All five were listed in the `MIGRATING` backlog. This is group C2-2 of the slop-removal campaign; the provenance surface and the shared resolver landed in `settings-provenance` (#2214).

## What Changes

- `dashboard_settings` gains five nullable columns of the same names. NULL inherits the environment value (or the code default) at read time; the first-boot seed and the migration never copy the environment into the row. A non-NULL value wins over the environment.
- `GET`/`PUT /api/settings` expose the five effective values with a `provenance` entry each; `PUT` is tri-state (omitted = unchanged, `null` = inherit, value = store). Bounds mirror the `Settings` fields; the in-flight penalty is additionally bounded at 100 because it is added to a percentage that saturates there. A dashboard lease TTL takes part in the same PUT-time timeout-invariant check as the dashboard-managed request budgets (`dashboard-managed-upstream-timeouts`): the `account-lease-ttl-covers-*` rules are evaluated on the merged effective values, so a TTL below an effective budget and a budget raised above an effective TTL are both rejected.
- The load balancer no longer reads `get_settings()` for these knobs. The proxy service resolves them once per selection or lease operation from the cached `dashboard_settings` snapshot it already holds for that operation (the same snapshot the concurrency caps come from) into a frozen `RoutingTunables` and passes it into `select_account`, `check_opportunistic_admission` and `acquire_account_lease` next to the routing strategy and the caps; the balancer threads it through every lock section without re-reading settings. Paths that have no request snapshot of their own (the stream error funnel that records overload rejections, an unkeyed bridge session reacquiring its lease) reuse the balancer's most recent request snapshot; the environment applies only before the first request has been served.
- The dashboard's routing card gains a "Routing weights and overload isolation" section in the Advanced group with an input per numeric knob, a switch for the error-rate weighting, an `InheritBadge` per field and a one-click return to inheritance.
- The five environment variables remain as deprecated fallbacks for one release (`T3 → dashboard` in the settings reference) and are removed from `Settings` in the next minor, when their names join `_REMOVED_SETTINGS`. Their `MIGRATING` entries are deleted now.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: the overload isolation and error-rate weighting requirements name the dashboard setting instead of the environment variable and gain the "Dashboard value overrides startup environment" scenario; a new requirement fixes how the routing weights (in-flight penalty, leased-token weight, lease TTL) and the overload isolation window are resolved and consumed (one snapshot per request, no settings read under a runtime lock).

## Impact

- Migration `20260909_050000_dashboard_routing_overload_settings` (five nullable columns, sqlite and PostgreSQL).
- Code: `app/modules/proxy/_load_balancer/tunables.py` (new), `load_balancer.py`, `_load_balancer/{overload_backoff,error_rate,sticky_selection,unbound_selection,opportunistic_admission}.py`, `proxy/service.py`, `_service/codex_control.py`, `_service/http_bridge/request_submit.py`; settings ORM, repository, service, schemas, API; `tiers.py`; frontend routing settings, schemas, three locales.
- API: additive fields on `GET`/`PUT /api/settings`.
- Operators: the five knobs are editable live; the environment variables keep working until the next minor release.
