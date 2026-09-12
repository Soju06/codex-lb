# Local database switch gate

## Why
The active database uses an older migration lineage than the current fork checkout. Readiness alone must not authorize a shared-database upgrade.

## What Changes
Add a read-only SQLite gate requiring the database revision, both single migration heads, migration file contents, and ORM definitions to match. This intentionally rejects upgrades needing schema changes. Allow the fixed acceptance validator to run this check from an operator-owned port/release plan.

## Impact
Local scripts only. No production migrations, schema stamping, or client routing changes.
