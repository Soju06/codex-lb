## ADDED Requirements

### Requirement: Alembic revision ID naming policy

All Alembic revisions SHALL use `YYYYMMDD_HHMMSS_<slug>` IDs and migration filenames SHALL match `<revision>.py`.

#### Scenario: Validate revision naming and filename
- **WHEN** migration policy checks run
- **THEN** each revision ID matches `^\d{8}_\d{6}_[a-z0-9_]+$`
- **AND** each migration filename equals `<revision>.py`

### Requirement: Automatic remap for legacy Alembic revision IDs

The system SHALL automatically remap known legacy Alembic revision IDs to timestamp-based revision IDs before applying upgrades.

#### Scenario: Startup sees known legacy Alembic revision ID
- **WHEN** startup migration finds legacy IDs in `alembic_version`
- **THEN** it remaps them to known timestamp-based revision IDs
- **AND** it continues with Alembic `upgrade head`

#### Scenario: Startup sees unsupported Alembic revision ID
- **WHEN** startup migration finds IDs that are neither current revisions nor known legacy IDs
- **THEN** it fails fast with an explicit error requiring manual intervention

### Requirement: Single Alembic head at merge gate

The project SHALL converge to a single Alembic head at merge/release gates.

#### Scenario: CI migration policy check detects multiple heads
- **WHEN** CI evaluates Alembic heads
- **THEN** the check fails
- **AND** the change must add an Alembic merge revision before merge/release

## MODIFIED Requirements

### Requirement: Migration drift guard in CI

The project SHALL fail CI when migration policy or ORM metadata and migrated schema diverge.

#### Scenario: Policy and drift check run
- **WHEN** CI executes migration checks
- **THEN** it upgrades a temporary database to `head`
- **AND** runs a unified check command that validates head count, revision naming policy, and schema drift
- **AND** fails if any policy violation or drift is detected
