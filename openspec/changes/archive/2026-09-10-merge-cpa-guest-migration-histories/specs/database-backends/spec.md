## ADDED Requirements

### Requirement: CPA and guest-session migration convergence

The migration graph SHALL converge at one head when guest-session generation and the published CPA/spool merge coexist. Upgrading from either existing head or both heads SHALL preserve all existing revision identities, source data, and settings while applying the missing schema. Guest generation SHALL default to zero only when newly added. A merge-only downgrade to either immediate parent SHALL preserve both schemas and data while restoring the two parent stamps.

#### Scenario: Upgrade an existing CPA database

- **WHEN** a populated CPA/spool database upgrades to the unified head
- **THEN** guest generation SHALL become available with default zero
- **AND** source identities, encrypted credentials, catalog state and retention settings SHALL remain intact

#### Scenario: Upgrade an existing guest-session database

- **WHEN** a populated guest-session database upgrades to the unified head
- **THEN** CPA catalog columns SHALL become available
- **AND** the stored guest generation, source identities and settings SHALL remain intact

#### Scenario: Merge-only downgrade and re-upgrade

- **WHEN** the unified head is downgraded to either immediate parent and upgraded again
- **THEN** both schemas and all populated data SHALL remain unchanged
- **AND** the revision stamps SHALL converge to the unified head again
