# Tasks: retention-dashboard-settings

## 1. Schema & migration

- [x] 1.1 Add nullable `request_log_retention_days` / `usage_history_retention_days` to `DashboardSettings` model
- [x] 1.2 Alembic migration on current head (upgrade + downgrade, no backfill)

## 2. Backend

- [x] 2.1 Settings API: response + update schemas with env-parity validators
- [x] 2.2 Repository/service wiring (effective-value fallback like proxy account caps)
- [x] 2.3 Retention job resolves effective retention through SettingsCache (dashboard wins, env alias fallback)
- [x] 2.4 Scheduler always ticks and re-evaluates effective retention per tick; leader gating unchanged
- [x] 2.5 Mark env fields deprecated in `settings.py` comment (keep them working)

## 3. Frontend

- [x] 3.1 Data retention card inside the existing Advanced settings group
- [x] 3.2 i18n keys in `en.json` and `zh-CN.json`
- [x] 3.3 Zod schemas + payload handling (fields only sent when edited)

## 4. Tests & docs

- [x] 4.1 Unit tests: scheduler runtime re-evaluation, settings service fallback
- [x] 4.2 Integration tests: settings API validation/persistence, retention precedence
- [x] 4.3 Frontend tests: new card, schema validation, settings page
- [x] 4.4 Screenshots of the settings page with the new card
