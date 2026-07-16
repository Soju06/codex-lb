## ADDED Requirements

### Requirement: Docker Compose Postgres profile

The Docker Compose `postgres` profile SHALL use a persistent named volume for Postgres data.

When the profile uses Postgres 18 or newer, the service SHALL mount that named volume at `/var/lib/postgresql`, the parent directory of the image's versioned `PGDATA` path.

The Compose configuration SHALL provide an explicit one-shot upgrade profile for existing pre-18 named volumes.

The `postgres-upgrade` service SHALL pin the upgrade helper image by digest because the helper mounts the same named Postgres data volume read-write and mutates the stored database cluster.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a pre-18 root-level `PG_VERSION` marker in the mounted named volume.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a nested `/var/lib/postgresql/data/PG_VERSION` marker with a pre-18 major version.

The normal Postgres service SHALL preserve runtime command arguments when it delegates to the official Postgres entrypoint.

The operator documentation SHALL describe how to stop the old service, back up the named volume, run the upgrade profile, start Postgres, and verify the upgraded database.

#### Scenario: Existing Postgres 16 volume is guarded

- **GIVEN** the named Compose volume contains a root-level `PG_VERSION` file from a Postgres 16 data directory
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator to run the `postgres-upgrade` profile

#### Scenario: Upgraded or fresh Postgres 18 volume starts normally

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service
- **THEN** the service delegates to the official Postgres entrypoint
- **AND** the Postgres 18 image initializes or opens the versioned data directory under `/var/lib/postgresql`

#### Scenario: Nested legacy data directory is guarded

- **GIVEN** the named Compose volume contains a nested `/var/lib/postgresql/data/PG_VERSION` file with a pre-18 major version
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator that the nested data directory must be upgraded before Postgres 18 starts

#### Scenario: Runtime command arguments are preserved

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service with runtime PostgreSQL command arguments
- **THEN** the guard delegates those arguments to the official Postgres entrypoint
