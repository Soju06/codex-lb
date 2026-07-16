# Change: retention-dashboard-settings

## Why

Item 13 of issue #1340 (settings-surface reduction): data-retention policy is
an operator decision that should be adjustable at runtime from the dashboard,
not an env-only knob that requires a restart. Every comparable per-deployment
policy (routing strategy, warmup, session TTL, account concurrency caps)
already lives in dashboard runtime settings backed by `dashboard_settings` +
`SettingsCache` with cross-replica invalidation.

## What Changes

- Two new nullable columns on `dashboard_settings`:
  `request_log_retention_days` and `usage_history_retention_days`
  (`NULL` = not set from the dashboard).
- Precedence: a non-NULL dashboard value wins; otherwise the env setting
  (now a deprecated one-release alias) applies; otherwise retention is
  disabled. `0` remains "disabled" at either layer.
- The dashboard settings API exposes both fields with the same validation
  floors as the env validators (0 or >= 30 for request logs, 0 or >= 45 for
  usage history, max 3650).
- The retention scheduler always runs its cheap hourly tick and re-evaluates
  the effective retention per tick through the SettingsCache-backed path, so
  a dashboard change takes effect without restart on every replica.
  Leader-election gating of the actual pass is unchanged.
- The Settings page gains a "Data retention" card inside the existing
  Advanced group (no new nav item).
- `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`
  stay functional this release as deprecated aliases; removal is a later
  phase (they are NOT added to `_REMOVED_SETTINGS` yet).

## Impact

- Affected specs: `data-retention`
- Affected code: `app/db/models.py`, Alembic migration,
  `app/modules/settings/*`, `app/core/retention/*`,
  `frontend/src/features/settings/*`
- DB: additive migration (two nullable columns, no backfill needed)
