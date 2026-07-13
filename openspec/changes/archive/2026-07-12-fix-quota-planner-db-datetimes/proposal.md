## Why

After deploying codex-lb 1.20.0-beta.3 to a Postgres-backed host (10.0.0.113),
the quota planner tick fails with:

```
Quota planner tick failed
TypeError: can't subtract offset-naive and offset-aware datetimes
```

The quota phase scheduler plans actions with timezone-aware UTC datetimes
(`datetime.now(timezone.utc)` and aware `scheduled_at` / `target_peak_at`), then
persists those aware values into the timezone-naive SQLAlchemy `DateTime`
columns `QuotaPlannerDecision.scheduled_at` and `QuotaPlannerDecision.executed_at`.
asyncpg/Postgres rejects mixing offset-aware and offset-naive datetimes on those
naive columns, so the planner loop raises and every tick is logged as failed.

SQLite silently stores the aware value, which is why the bug only surfaced in
production on Postgres.

## What Changes

- Normalize timezone-aware datetimes to naive UTC before they are written to the
  timezone-naive `QuotaPlannerDecision.scheduled_at` and `executed_at` columns,
  in both the insert path (`log_decision`) and the update path
  (`update_decision_status`).
- Preserve naive inputs unchanged so existing callers keep working.
- Keep externally visible JSON audit snapshots (e.g. `target_peak_at`,
  `scheduled_at` inside `state_before_json`) as ISO strings with timezone
  offsets; this fix only sanitizes the DB-bound column values, not audit JSON.
- Add regression coverage proving the repository sanitizes aware values before
  persistence, guarding the Postgres path that failed in production.

## Impact

- Restores quota planner ticks on Postgres deployments.
- Aligns quota planner persistence with the repository-wide convention of
  storing naive UTC in `DateTime` columns (see `app/core/utils/time.py`).
- No schema migration: columns remain timezone-naive `DateTime`; only the
  values bound to them are normalized.
