## ADDED Requirements

### Requirement: OAuth Live policy is a global singleton

The database SHALL store at most one global OAuth Live policy with singleton id `1` and an explicit set of allowed upstream Accounts. Policy rows MUST contain no credential, caller identity, email, call id, SDP, or frame content. Allowed Account deletion SHALL cascade its relationship row while preserving the singleton policy.

#### Scenario: New installation starts disabled

- **WHEN** a database upgrades to the OAuth Live policy revision
- **THEN** the global policy API returns inactive with an empty pool
- **AND** Key-based Live behavior remains available

#### Scenario: Downgrade removes the global policy schema

- **WHEN** the global revision downgrades
- **THEN** the global relationship table is removed before the singleton table
- **AND** the database has no OAuth Live policy tables
