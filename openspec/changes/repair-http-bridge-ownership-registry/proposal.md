# Change: Repair HTTP bridge ownership registry bootstrap

## Why

Some databases stamped through the HTTP bridge recovery revisions contain the
parent operation index but not the shared ownership-marker table.  The latest
image then fails its post-migration drift guard and restarts indefinitely.

## What Changes

- Add a forward-only Alembic repair revision after the persisted recovery
  schema repair.
- Ensure the shared HTTP bridge migration ownership table exists even when the
  parent index already exists.
- Add regression coverage for the migration graph and bootstrap path.

## Non-Goals

- No data rewrite, index replacement, or ownership-marker backfill.
- No downgrade that removes a shared ownership table used by parent revisions.
- No application request-routing or replay-policy change.

## Impact

- Affected spec: `database-migrations`.
- Affected code: Alembic migration and migration unit tests.
- The repair is idempotent and safe for rolling deployment.
