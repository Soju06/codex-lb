# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260909_120000_dashboard_conversation_archive`: nullable BOOLEAN `conversation_archive_enabled` on `dashboard_settings`; ORM column; repository seeds NULL and applies the tri-state update.
- [x] 1.2 `DashboardSettingsData` / `Response` / `UpdateRequest` carry the effective boolean and provenance via `resolve_inheritable`; `Response` adds the read-only admin-only `conversationArchiveDir`; the env field keeps the `# T3 → dashboard (deprecated env alias, remove next minor)` marker; `tiers.MIGRATING` entry removed; `check_settings_tiers` passes.
- [x] 1.3 `archive_enabled()` resolves the toggle from `SettingsCache.cached_row()` (environment layer before the first load); the 15 + 5 upstream-client call sites are unchanged (grep gate). Telemetry `features.conversation_archive` reports the effective value.
- [x] 1.4 Settings API writes the `conversation_archive_toggled` audit event with `enabled`, `source`, `actor`, `actor_role` on every effective flip; ownership-only changes stay in `settings_changed.changed_fields`.

## 2. Dashboard

- [x] 2.1 `conversationArchiveEnabled` / `conversationArchiveDir` on the settings schema (nullable toggle on the update request); factories updated.
- [x] 2.2 `ConversationArchiveSettings` card under Advanced next to Data retention: confirmation dialog gates `true`, off saves immediately, `InheritBadge` with reset blocked when the env alias would enable recording, read-only per-replica directory; `settings.conversationArchive.*` strings in `en`, `ko`, `zh-CN`.
- [x] 2.3 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver states (NULL→env, NULL+env unset→default, dashboard wins both ways), env fallback before the cache loaded, grep gate on the call sites, service provenance, telemetry effective value. Integration: API round trip (default → dashboard → cleared/env → unchanged on omit, column stays NULL, directory read-only), audit event asserted on enable and on clear-to-off with actor, enable → next record archived / disable → no new record without a restart, migration upgrade/downgrade, guest `GET /api/settings` redacts the directory.
- [x] 3.2 Frontend: cannot enable without confirming, confirm sends `true`, cancel sends nothing, off is immediate, reset blocked when the env alias would enable, page mock; i18n parity.
- [x] 3.3 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, simplicity budgets, `openspec validate dashboard-managed-conversation-archive --strict`, `docs/reference/settings.md` regenerated.
