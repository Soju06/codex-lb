## Context

The existing `app.db.migrate` CLI upgrades and checks databases. It does not seed fixtures or save benchmark results. #2307's unmerged progress logging is not required by this design.

## Goals / Non-Goals

Provide an owned disposable PostgreSQL experiment with reproducible inputs and per-revision subprocess wall times. Preserve the existing Alembic runner. Mandatory duration limits, a reference runner, schedules and runtime data phases are excluded.

## Decisions

The command requires an explicit disposable-target acknowledgement, database URL and exact base/target Alembic revision IDs. Both database environment variables are pinned before app imports or subprocesses. A nonempty database is rejected instead of reset. Operators dispose of the database after each run.

Use deterministic set-based fixture insertion at the base schema with explicit row/account counts and seed. Reflect the historical schema to include supported optional fields without importing current ORM defaults. Record the distribution algorithm and observed fixture aggregates.

Use Alembic's revision graph to select unapplied ancestors and call `python -m app.db.migrate upgrade REVISION` for each step. Each subprocess includes interpreter startup, graph inspection, locking and transaction overhead. Each step commits separately, releases its lock and can leave earlier steps committed after failure. These measurements do not represent a single production upgrade transaction or concurrent production load.

Save an initially incomplete report before database work and update it on failure or completion. Never overwrite a previous report directory. Record revision stamps and public `check` results; check compares to current ORM head and can report expected drift for historical targets without changing them.

## Risks / Trade-offs

- Synthetic distributions omit many production tables and traffic effects. Publish their limits alongside timings.
- Historical schemas can require unknown fields. Fail explicitly instead of inventing values.
- A supplied target could be misidentified. Require explicit ownership acknowledgement and an empty public schema, with no reset/drop mode.
- Output must not leak credentials. Record a password-free endpoint identity and diagnostic categories rather than raw subprocess errors.
