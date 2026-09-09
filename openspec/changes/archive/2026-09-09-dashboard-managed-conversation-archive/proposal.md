# Change: dashboard-managed-conversation-archive

## Why

`conversation_archive_enabled` turns the proxy into a full recorder of every prompt and response body relayed upstream, readable by any dashboard admin from request-log details. It is a behaviour toggle an operator flips for an audit or a support case and turns off again, yet it exists only as `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED`, so every flip is a rollout and a restart of every replica. `configuration-tiers` classifies it T3 and lists it in `MIGRATING`. Because the toggle is security-sensitive, moving it into the dashboard needs three safeguards the environment variable never had: an explicit confirmation before enabling, a dedicated audit event with the actor for every effective on/off change, and an honest statement that the archive directory is a per-replica local shard.

## What Changes

- One nullable `dashboard_settings` BOOLEAN column of the same name (alembic `20260909_120000_dashboard_conversation_archive`). NULL means "inherit": the deprecated `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED` alias, then the code default (off). Nothing is seeded from the environment (`configuration-tiers` P-D). Default behaviour is unchanged.
- `GET`/`PUT /api/settings` expose `conversationArchiveEnabled` (effective value) plus `provenance.conversation_archive_enabled` through the shared `resolve_inheritable` resolver and accept the tri-state update (omitted = unchanged, `null` = back to inherited, boolean = dashboard value). `GET` also returns `conversationArchiveDir`, this replica's T1 archive directory, read-only and only for admin principals. The env field stays one release as a deprecated alias (`# T3 → dashboard (deprecated env alias, remove next minor)`) and leaves `MIGRATING`.
- The archive writer's single gate `archive_enabled()` resolves the toggle from `SettingsCache.cached_row()` (the last loaded dashboard snapshot; the environment layer before the first load) instead of `get_settings()`. The fifteen `archive_*` call sites in `app/core/clients/proxy.py` and five in `proxy_websocket.py` are unchanged; none reads the database, awaits, or resolves the toggle itself. A dashboard flip takes effect within the settings-cache TTL on every replica without a restart. The telemetry feature flag reports the same effective value.
- Audit: the settings API writes a dedicated `conversation_archive_toggled` audit event (`enabled`, `source`, `actor`, `actor_role`, actor IP) whenever the effective value flips on or off, in addition to the `settings_changed` entry; storing the same value again or an ownership-only change is not a flip.
- Dashboard: a "Conversation archive" card under Settings → Advanced next to Data retention with the shared `InheritBadge`. Turning the switch on opens a confirmation dialog ("All prompt/response bodies will be written to each replica's local archive directory …"); only the dialog's confirm action sends `true`, turning off saves immediately, and "Reset to inherited" is blocked when the environment alias would silently start recording. The card shows the archive directory read-only with a "per-replica local shard" note. Strings in `en`, `ko`, `zh-CN`.
- `docs/reference/settings.md` marks the setting `T3 (dashboard)`; `docs/configuration.md` names the card and its safeguards.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: MODIFIED requirement "Full upstream conversation archive" — the archive is enabled by the dashboard setting (environment variable as deprecated fallback while unset), resolved from the settings-cache snapshot at the writer's single gate, with the enabling scenario rewritten and a scenario for turning it off without a restart.
- `audit-logging`: ADDED requirement — every effective on/off change of the conversation archive is a dedicated audit event naming the actor.
- `configuration-tiers`: unchanged (this change realises its `MIGRATING` procedure for one field).

## Impact

- Schema: one nullable column on `dashboard_settings` (SQLite and PostgreSQL, guarded add/drop).
- API: additive fields on `GET`/`PUT /api/settings`; older dashboards ignore them, the new dashboard tolerates their absence.
- Code: `app/core/conversation_archive.py` (gate + resolver + audit action name), `app/modules/telemetry/snapshot.py`, settings module (`models`, `repository`, `service`, `schemas`, `api`), `app/core/config/{settings,tiers}.py`, frontend `features/settings` (schemas, `conversation-archive-settings.tsx`, page), three locales.
- Operators: no action. `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED` keeps working until the dashboard value is set; the reference page says so. The env field is removed in the next minor via `_REMOVED_SETTINGS`. `CODEX_LB_CONVERSATION_ARCHIVE_DIR` stays environment-only (T1, per replica).

Part of the slop-removal campaign 0908 (M5).
