## Why

Security-work authorization is a monotonic property of a response lineage, but
older schemas attach that property only to the account that happened to own the
lineage. Deleting or replacing that account can therefore erase the requirement
and let a durable bridge or sticky alias be reused by an unauthorized account.

Deployments that ran the broader live integration stack can also contain the
new lineage and compatibility columns without a matching focused mainline
revision. Those databases need one idempotent Alembic carrier rather than a
second migration head or a destructive schema rollback.

## What Changes

- Add one current-head Alembic revision that reconciles the security-lineage,
  durable bridge, pending tool-call, and retained quota-planner compatibility
  columns idempotently.
- Persist the security-work requirement on durable bridge rows and detached
  `codex_session` markers so it survives account deletion and reassignment.
- Preserve detached security markers during ordinary sticky-session cleanup.
- Keep account-less security-lineage usage rows out of normal account usage
  windows while retaining them as migration-compatible evidence.
- Keep the migration downgrade non-destructive because it cannot prove which
  previous live aggregate created an existing column or marker.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: reconcile and retain monotonic security-lineage state
  across fresh databases and databases that already ran the broader live stack.

## Impact

- Affected code: SQLAlchemy metadata, the durable bridge and sticky-session
  repositories, usage-row mapping, and one Alembic revision.
- Affected data: existing security-required owners and durable aliases are
  backfilled into detached hashed markers; no marker is downgraded or deleted.
- APIs and configuration: none.

