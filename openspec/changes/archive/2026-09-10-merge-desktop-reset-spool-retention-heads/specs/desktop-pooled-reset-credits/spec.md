## ADDED Requirements

### Requirement: Reset and spool-retention migration composition preserves data

The database MUST upgrade to one head from either the Desktop reset merge revision or dashboard spool-retention revision. Existing reset owner/credit bindings, reset policy and explicit spool-retention settings MUST survive upgrade. Downgrading only the composition merge MUST preserve both parent schemas and their data.

#### Scenario: Existing reset database adopts spool retention

- **WHEN** an existing reset database upgrades to the composition head
- **THEN** its reset policy and exact redemption bindings remain unchanged
- **AND** the new retention column is nullable and inherits the existing policy

#### Scenario: Existing retention value survives merge downgrade

- **WHEN** a database with an explicit retention value upgrades and downgrades only the composition merge
- **THEN** the value and any redemption bindings remain unchanged
- **AND** both parent revisions remain stamped
