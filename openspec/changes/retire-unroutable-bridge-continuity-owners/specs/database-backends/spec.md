## ADDED Requirements

### Requirement: Bridge sessions carry continuity-retirement markers

`http_bridge_sessions` MUST carry a nullable retirement timestamp and a nullable retirement
scope, matching the pair `sticky_sessions` already carries in both name and meaning. The
migration MUST add them as nullable with no default and MUST NOT backfill, so every row that
exists at upgrade time keeps hard ownership until a writer retires it and a replica running the
previous build is unaffected. Both columns MUST be reversible by the revision's downgrade, and
the revision MUST leave the Alembic graph on a single head.

#### Scenario: Upgrading an existing database

- **WHEN** the revision is applied to a database that already has bridge sessions
- **THEN** both columns exist, are nullable, and have no default
- **AND** every pre-existing session still resolves to its recorded owner

#### Scenario: Downgrading

- **WHEN** the revision is reverted
- **THEN** both columns are gone and the remaining schema matches the parent revision

#### Scenario: Re-running against a partially migrated database

- **WHEN** the upgrade runs against a database where one or both columns already exist
- **THEN** it completes without error and leaves the columns in place
