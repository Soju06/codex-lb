# Change: dashboard-managed-stream-bridge-budgets

## Why

`http_responses_stream_request_budget_seconds` (the 2-hour ceiling of a streaming Responses turn) and `http_responses_session_bridge_request_budget_seconds` (the 2-hour ceiling of an HTTP session bridge request) are T3 behaviour tunables (`configuration-tiers`) that an operator has a real reason to change while the proxy is running — a fleet whose longest agentic turns outgrow the default, or one that wants stuck bridge work fenced sooner — yet they live only in the process environment: changing one means editing every replica's environment and restarting it. They are the M1 group of the `MIGRATING` backlog (slop-removal 0908 triage) and sit next to the C2-1 upstream timeouts they are checked against, so they join the same dashboard card, the same provenance surface and the same PUT-time invariant check.

## What Changes

- `dashboard_settings` gains two nullable `FLOAT` columns named after the `Settings` fields. NULL inherits the environment value (or the 7200 s code default); a non-NULL column wins on every replica. The environment is never copied into the column.
- `GET`/`PUT /api/settings` expose the two effective values (`httpResponsesStreamRequestBudgetSeconds`, `httpResponsesSessionBridgeRequestBudgetSeconds`) plus a `provenance` entry each, with the existing tri-state PUT contract (omitted = unchanged, `null` = clear to inherit, value = store) and the C2-1 bounds (`> 0`, `<= 86400`) on the update request only.
- The two names join `DASHBOARD_TIMEOUT_SETTINGS`, so `PUT /api/settings` evaluates the startup timeout invariants on the effective values before and after the change. Two new rules, `upstream-connect-within-stream-budget` and `upstream-connect-within-bridge-budget` (`upstream_connect_timeout_seconds <=` each budget — both paths spend the connect timeout inside their budget), join the existing `admission-wait-within-stream-budget` and `bridge-stuck-gate-retire-within-bridge-budget` (`2 x 300 s fixed stuck gate < bridge budget`) rules; only violations the change introduces are rejected (`400 timeout_invariant_violation`).
- Consumers: every request-path reader already goes through the proxy service settings facade (`with_dashboard_overrides`) and picks the dashboard value up with no call-site change (stream deadline, bridge request budget and eventless budget, lease stale TTL, bridge forwarding). Two background readers are switched from the bare environment value to a dashboard snapshot: the quota warm-up claim lease floor (`_warmup_claim_ttl_seconds`, now resolved from the per-tick snapshot the scheduler passes into `warm_now`, which is also bound around the probe itself so the stream it opens uses the same effective budget the lease floors at) and the HTTP bridge stale-operation abandonment sweep (`abandon_stale_http_bridge_operations`, one `SettingsCache` read per heartbeat pass via `effective_settings`, falling back to the environment value when the snapshot cannot be read). No database read inside a runtime lock or per-item loop.
- Dashboard: the two budgets join the existing "Upstream timeouts" card (Settings → Advanced) with the `InheritBadge`, effective-value placeholder and validation mirroring the backend (connect within the stream and bridge budgets; bridge budget above 600 s; stream budget at or above the fixed 10 s admission wait). Strings in `en`, `ko`, `zh-CN`.
- `app/core/config/tiers.py`: the two `MIGRATING` entries are removed; the `Settings` fields stay as deprecated env aliases (`# T3 → dashboard (deprecated env alias, remove next minor)`). No default changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: MODIFIED "Long Codex websocket turns tolerate extended upstream silence" (the stream request budget is dashboard-managed with the fixed precedence; the connect timeout is bounded by its effective value), MODIFIED "Ambiguous HTTP bridge operations converge after owner loss" (the maintenance sweep resolves the bridge budget from a dashboard snapshot), MODIFIED "The pre-response silence budget is settings-derived" (the two settings-derived terms — the idle timeout and the bridge budget — are read as effective dashboard-managed values; the stuck gate is named as the fixed 300 s constant, mirroring the pending `constantize-session-bridge-tunables` block so neither archive reverts the other).
- `configuration-tiers`: no delta — the provenance shape and precedence are already specified; this change applies them to two more settings.

## Impact

- Schema: alembic `20260909_080000_dashboard_stream_bridge_budgets` (two nullable `FLOAT` columns; downgrade drops them), `down_revision = 20260909_070000_automation_run_claim_budget`.
- Code: `app/core/config/dashboard_overrides.py` (registry), `app/core/timeout_invariants.py` (two rules, refreshed source anchors), `app/modules/settings/{service,schemas,api,repository}.py`, `app/db/models.py`, `app/modules/quota_planner/{warmup,scheduler}.py`, `app/modules/proxy/_service/http_bridge/session_registry.py`, `app/core/config/{settings,tiers}.py`.
- API: additive fields on `GET`/`PUT /api/settings`; one new invariant rule id in `400 timeout_invariant_violation` messages.
- Operators: `CODEX_LB_HTTP_RESPONSES_STREAM_REQUEST_BUDGET_SECONDS` and `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_REQUEST_BUDGET_SECONDS` keep working as fallbacks for one release; the dashboard value wins once set.

Part of the slop-removal campaign 0908 (MIGRATING triage, M1).
