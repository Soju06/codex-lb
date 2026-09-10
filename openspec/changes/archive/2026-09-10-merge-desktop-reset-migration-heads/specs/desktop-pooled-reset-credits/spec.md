## ADDED Requirements

### Requirement: Reset pooling shares one migration head with main

The database MUST upgrade to one Alembic head from either the reset-pooling revision or the main revision. Upgrading MUST preserve existing settings and redemption bindings. Downgrading only the merge MUST preserve both parent schemas and their data.

#### Scenario: Existing reset binding survives integration

- **WHEN** a database at the reset-pooling revision contains a redemption binding and upgrades to head
- **THEN** the original owner and credit binding remain unchanged
- **AND** the resulting schema matches the application models

#### Scenario: Main database enables the new schema

- **WHEN** a database at the main revision upgrades to head
- **THEN** reset pooling defaults to disabled and existing settings remain unchanged

#### Scenario: Operator reverses only the merge

- **WHEN** the merge revision downgrades to either immediate parent
- **THEN** both parent stamps and all settings and bindings remain intact
