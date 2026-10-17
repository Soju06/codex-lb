# Change: dashboard-managed-spool-retention

## Why

The durable HTTP bridge operation spool stores the raw request payload of every bridged turn together with the response events it may have to replay. How long that prompt material is kept is set by `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_RETENTION_SECONDS` alone, so an operator who wants prompts deleted sooner — the ordinary privacy answer — has to edit the environment and restart every replica. `configuration-tiers` classifies retention as T3 and lists this field in `MIGRATING`; the sibling `constantize-session-bridge-tunables` change deliberately left it out of scope because it carries sensitive data rather than being a never-tuned internal.

Shortening it is not free: the spool is the replay source for durable bridge recovery, so below some window the retention sweep deletes transcripts a still-live reader needs. That window is derivable, and today nothing states or enforces it.

## What Changes

- One nullable `dashboard_settings` FLOAT column of the same name (alembic `20260910_010000_dashboard_spool_retention`). NULL means "inherit": the deprecated environment alias, then the code default (7 days). Nothing is seeded from the environment (`configuration-tiers` P-D). Default behaviour is unchanged.
- `GET`/`PUT /api/settings` expose `httpResponsesSessionBridgeOperationSpoolRetentionSeconds` (effective value) plus `provenance.http_responses_session_bridge_operation_spool_retention_seconds` through the shared `resolve_inheritable` resolver, and accept the tri-state update (omitted = unchanged, `null` = back to inherited, value = dashboard value). The env field stays one release as a deprecated alias (`# T3 → dashboard (deprecated env alias, remove next minor)`) and leaves `MIGRATING`.
- A PUT-time floor: the effective retention MUST cover every window in which a spooled operation can still be read — the bridge session reuse window (the existing `_abandoned_bridge_retention_seconds` terms), the stale-operation abandonment window (`max(1800s, the effective bridge request budget)`) and the lifetime of an already-claimed retry circuit (two circuit TTLs, because a claim leaves the row timestamp untouched). The floor is derived, not a literal, because two of its terms are themselves dashboard settings; it is evaluated on the effective values before and after the change, so only a violation the change introduces is rejected. A deployment can already be below the floor without any update having passed the check (the environment alias alone decides the window while the column is NULL); that state does not make the surface read-only and does not suspend the check either — while below the floor an update is accepted only when it leaves the retention no shorter and the floor no higher than it found them ("no worse"). Startup warns about the state on the existing warn-only effective-value pass, so the first refusal is never a surprise. `GET` reports the floor so the dashboard can mirror the check.
- The two consumers — the startup one-shot purge and the leader-gated cleanup scheduler's retention pass — resolve the window from the dashboard snapshot the pass already holds, once per pass, never inside a runtime lock and never per operation. A dashboard change applies on the next tick without a restart.
- Dashboard: the field joins the existing **Data retention** card with the shared `InheritBadge`, reset-to-inherited, a label that states what the spool holds, the derived floor shown as a hint, and a client-side mirror of the floor check. Strings in `en`, `ko`, `zh-CN`.
- `docs/reference/settings.md` marks the setting `T3 (dashboard)`; `docs/configuration.md` names the card and the floor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: MODIFIED requirement "Continuous transcript retention" — the retention window is dashboard-managed (environment variable as a deprecated fallback while unset), read once per pass from the snapshot the pass already holds, and bounded below by the replay floor.
- `data-retention`: ADDED requirement — the Data retention card also manages the bridge operation spool window, whose validated floor is derived from the replay windows rather than fixed like the request-log and usage-history floors.
- `configuration-tiers`: unchanged (this change realises its `MIGRATING` procedure for one field).

## Impact

- Schema: one nullable column on `dashboard_settings` (SQLite and PostgreSQL, guarded add/drop).
- API: additive fields on `GET`/`PUT /api/settings`; older dashboards ignore them, the new dashboard tolerates their absence. One new `400` code, `spool_retention_below_floor`.
- Code: new `app/core/config/spool_retention.py` (setting name, resolver, floor terms) which `app/modules/sticky_sessions/cleanup_scheduler.py` also uses for its existing `_abandoned_bridge_retention_seconds`, `app/main.py`, settings module (`models`, `repository`, `service`, `schemas`, `api`), `app/core/config/tiers.py`, frontend `features/settings` (schemas, `data-retention-settings.tsx`), three locales.
- Operators: no action. The environment variable keeps working until a dashboard value is set; the reference page says so. The env field is removed in the next minor via `_REMOVED_SETTINGS`.

Part of the slop-removal campaign 0908 (MIGRATING triage, R2).
