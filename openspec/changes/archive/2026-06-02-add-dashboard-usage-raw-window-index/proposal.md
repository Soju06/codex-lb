## Why
`GET /api/dashboard/overview?timeframe=7d` performs latest-usage lookups for both primary and secondary usage windows. On SQLite, the secondary-window lookup filters on the raw `usage_history.window` column, but the existing latest-usage index is built on `coalesce("window", 'primary')`. With larger `usage_history` tables this causes SQLite to choose a slower scan path for secondary latest usage.

## What Changes
- Add a forward-only Alembic migration that creates `idx_usage_window_raw_account_latest` on `usage_history ("window", account_id, recorded_at DESC, id DESC)`.
- Add the index to SQLAlchemy metadata and migration drift checks so it remains part of the managed schema.
- Keep the migration idempotent with `CREATE INDEX IF NOT EXISTS`, matching the live database hotfix path where the index may already exist before the migration is applied.
- Add regression coverage for drift detection, idempotent migration application, and the SQLite query plan for secondary latest usage.

## Impact
- Improves the dashboard overview secondary latest-usage query on SQLite without changing dashboard response semantics.
- A live SQLite hotfix with this index reduced the secondary latest-usage query from about 2300 ms to about 720 ms,
  roughly a 3.2x improvement for that database read path.
- The same live probe reduced total SQL time inside `DashboardService.get_overview("7d")` from about 4.6 seconds
  to about 3.0 seconds, roughly a 1.5x improvement for the measured SQL portion.
- The full dashboard overview remained around 12 seconds in the direct service probe because most remaining time is
  spent loading and processing large secondary usage-history windows for depletion and weekly pace calculations.
- Increases SQLite write/index maintenance by one additional index on `usage_history`.
- Does not address the remaining overview cost from loading and processing large usage-history windows for depletion and weekly pace.
