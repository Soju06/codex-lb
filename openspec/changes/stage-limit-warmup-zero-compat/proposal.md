## Why

The threshold activation PR needs to introduce an active `0%` default without
breaking a rolling deployment in which an old replica still rejects zero in its
settings schemas. The compatibility contract must land first so both API
directions understand zero before the activation migration starts emitting it.

## What Changes

- Accept `0` in the backend settings response/update schemas and the frontend
  response/update schemas, while keeping the dashboard control at a minimum of
  `1` and the public/default response at `99` for this preparatory release.
- Normalize a direct `PUT` value of `0` to the legacy `99` representation during
  this stage, so the compatibility release accepts the future value without
  emitting or persisting the active sentinel.
- Add a nullable, inactive `limit_warmup_reset_threshold_percent` database
  column and map it internally without making it the runtime source of truth.
- Add migration, API, frontend-schema, and downgrade coverage for mixed-version
  compatibility; do not backfill or enable the new column yet.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: stage a nullable compatibility column for the future
  reset-threshold cutover without changing the legacy column's value or default.
- `frontend-architecture`: settings API contracts accept the future zero value
  during rollout while the dashboard remains a positive-value control and the
  compatibility response remains `99`.

## Impact

- `DashboardSettings` ORM mapping and a forward-only Alembic migration.
- Settings repository/API schemas and integration tests.
- Dashboard Zod schemas and schema tests; no visible control or default change.
- OpenSpec migration and dashboard contract documentation.
- The follow-up activation PR will depend on this PR and can then initialize
  the compatibility column to `0`, make it non-null/defaulted, and switch the
  runtime source of truth without triggers, sentinels, or dual writes.
