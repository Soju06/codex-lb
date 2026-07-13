## Why

Quota planner repository methods accept caller-supplied datetimes for warmup
counts, cost aggregation, demand bins, and window observations. Timezone-aware
values can cross the database boundary from API or scheduler code, but database
comparisons and inserts expect the project-standard naive UTC representation.

## What Changes

- Normalize quota planner repository datetime inputs to naive UTC before
  binding them into database comparisons or persisted observation rows.
- Cover aware datetime inputs across warmup counts, warmup cost aggregation,
  demand bin aggregation, and quota window observations.

## Impact

- PostgreSQL and SQLite planner queries receive the same UTC-normalized
  boundary values.
- Operators avoid planner failures or missed demand windows caused by mixed
  aware and naive datetime inputs.
