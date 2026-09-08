## Why

The Codex HTTP-bridge prewarm canary experiment retired in
`reduce-settings-surface-phase-4` (issue #1340 phase 4, shipped in v1.22.0).
Its `request_logs.prewarm_canary_bucket` / `prewarm_eligible_reason` columns
have been unwritten since, but the `RequestLog` ORM model still maps them.
That mapping is what blocks the queued Alembic drop: the Helm migration Job is
a `pre-upgrade` hook that runs before the old replicas drain, and SQLAlchemy's
ORM INSERT renders an explicit `NULL` for every mapped column that has no
default, so a replica running the previous release would fail every request
log insert (and every full-entity request-log read) against a schema whose
columns are already gone. The columns can only be dropped one release after
the last release that maps them.

## What Changes

- Remove `prewarm_canary_bucket` and `prewarm_eligible_reason` from the
  `RequestLog` ORM model. Nothing reads or writes them.
- Keep the physical columns in the schema for this release and add them to
  `_LEGACY_EXTRA_COLUMNS` in `app/db/migrate.py`, so the schema-drift gate
  (`codex-lb-db check`, `make migration-check`, the PostgreSQL contract test)
  treats the retained columns as a known, allow-listed drift — the same
  mechanism already used for `request_logs.slim_summary_json`.
- Re-queue the destructive step: the Alembic drop revision plus removal of the
  two allow-list entries ships in the release after this one, once no
  supported replica maps the columns (next-release queue item 1 in
  `openspec/specs/deployment-installation/context.md`).

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: the request-log ORM model no longer carries
  the legacy canary bucket / eligibility cohort attributes; the physical
  columns remain insertable for legacy replicas until the follow-up drop.

## Impact

`app/db/models.py`, `app/db/migrate.py` (drift allow-list), the migration
test suites, and the observability / deployment context notes. No Alembic
revision, no runtime behavior change, and no rolling-upgrade or rollback
caveat: a previous-release replica still finds every column it maps, and this
release never touches the two columns.
