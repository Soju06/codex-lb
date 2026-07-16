## Why

The Compose `postgres` profile now uses Postgres 18, whose official image stores data under a versioned `PGDATA` path below `/var/lib/postgresql`. Existing Compose users may already have a named volume created by the Postgres 16 image at the old data-root layout. Starting Postgres 18 directly against that unupgraded volume can initialize a fresh empty cluster instead of upgrading the old one.

## What Changes

- Mount the Postgres named volume at `/var/lib/postgresql`, matching the Postgres 18 image layout.
- Add a one-shot `postgres-upgrade` Compose profile using a digest-pinned `pgautoupgrade/pgautoupgrade:18-alpine` image because that helper mounts and rewrites the operator's Postgres data volume.
- Make the normal `postgres` service fail fast when it sees the old root-level `PG_VERSION` marker.
- Document the backup, upgrade, start, and `codex-lb-db check` verification sequence.

## Impact

Operators with existing Postgres 16 Compose volumes get an explicit upgrade path and a guard against accidentally starting Postgres 18 before the volume is upgraded. Fresh Compose installs continue to initialize normally in the Postgres 18 layout.
