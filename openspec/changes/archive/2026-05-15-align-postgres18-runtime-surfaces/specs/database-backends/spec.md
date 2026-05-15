## ADDED Requirements

### Requirement: Local and CI PostgreSQL surfaces track PostgreSQL 18

Local Compose and PostgreSQL-backed CI jobs MUST use PostgreSQL 18 for the
supported PostgreSQL coverage path. The local Compose PostgreSQL profile MUST
mount persistent data at the PostgreSQL 18 Docker image's supported parent data
directory and MUST preload `pg_stat_statements` when query-level runtime tuning
is part of the sustained deployment profile.

#### Scenario: Local Compose PostgreSQL uses PostgreSQL 18

- **WHEN** an operator renders or starts the local Compose PostgreSQL profile
- **THEN** the PostgreSQL service uses a PostgreSQL 18 image
- **AND** the named PostgreSQL data volume is mounted at `/var/lib/postgresql`
- **AND** the service starts PostgreSQL with `pg_stat_statements` preloaded

#### Scenario: PostgreSQL CI jobs use PostgreSQL 18

- **WHEN** CI runs PostgreSQL-backed tests or migration checks
- **THEN** the PostgreSQL service image uses PostgreSQL 18
