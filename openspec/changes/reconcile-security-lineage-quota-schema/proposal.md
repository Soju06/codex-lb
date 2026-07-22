## Why

Deployments that previously ran the wider security-work live stack can already
have quota-planner settings columns that were later removed from the focused
mainline branch. A current security-work upgrade against that database shape
passes the logical Alembic lineage but leaves ORM/schema drift, causing the
startup drift guard or deploy preflight to fail.

## What Changes

- Retain the live lineage quota-planner settings columns in ORM metadata.
- Extend the existing security-lineage reconcile migration so installs that
  have the quota-planner table but lack those columns receive them
  idempotently.
- Keep downgrade non-destructive because the reconcile revision cannot prove
  which previous aggregate originally created the columns.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: the security-work migration lineage must reconcile
  legacy live-stack quota-planner columns without schema drift.

## Impact

- Affected code: SQLAlchemy ORM metadata and one existing Alembic reconcile
  revision.
- Affected data: no data rewrite; only missing compatibility columns are added
  with defaults.
- APIs and configuration: none.
