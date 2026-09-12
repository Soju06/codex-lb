## ADDED Requirements

### Requirement: Dropping the legacy dashboard credentials states the drain requirement and blocks nothing

Before applying any DDL, `run_upgrade()` — the single entry point shared by `python -m app.db.migrate upgrade`, the container entrypoint, the Helm pre-upgrade migration Job and `run_startup_migrations()` — MUST read the stamped revision with the existing side-effect-free inspection and decide whether the pending upgrade would cross the revision that drops the legacy dashboard credential columns. Whenever it would and the ledger is non-empty, the upgrade MUST emit exactly one warning stating that replicas of any earlier release must be stopped first, because they map the dropped columns and their settings reads and credential mirrors both fail once the columns are gone; a fresh install, and an upgrade that stops short of the drop, MUST emit nothing. The revision that drops the columns MUST descend from `20260909_020000_reproject_compat_admin_credentials`, so a database stamped anywhere below both reaches head in one command with its dashboard credentials copied onto the account rows before the columns they came from disappear. That ordering MUST be asserted by a test rather than re-checked at run time, and the upgrade MUST NOT be refused, gated behind a flag, an environment variable or a `Settings` field, or made conditional on the backend: `run_startup_migrations()` is the boot path, so refusing there would stop every install of an earlier release from starting, and the two-step upgrade it would demand applies exactly the same revisions in the same order. The warning MUST NOT be described as detecting a running previous-release replica: no such signal exists, and the guard proves only what the ledger says.

#### Scenario: A database from an older release upgrades in one step

- **GIVEN** a database stamped at a revision that precedes `20260909_020000_reproject_compat_admin_credentials`, whose legacy `dashboard_settings` columns still hold the dashboard password hash and TOTP counter
- **WHEN** the upgrade to head is run
- **THEN** it applies the whole chain in order and reaches head
- **AND** the bootstrap account row carries the password hash and TOTP counter the legacy columns held, and those columns are gone

#### Scenario: The drain requirement is stated by the process that performs it

- **GIVEN** a database that has already applied the reprojection and carries application data
- **WHEN** the upgrade crosses the drop revision
- **THEN** exactly one warning names the requirement to stop replicas of earlier releases first
- **AND** a fresh install applying the whole chain emits no such warning

#### Scenario: An upgrade that stops short of the drop says nothing

- **GIVEN** a database stamped below the drop revision
- **WHEN** the requested target is a revision below the drop
- **THEN** no drain warning is emitted and no DDL is refused
