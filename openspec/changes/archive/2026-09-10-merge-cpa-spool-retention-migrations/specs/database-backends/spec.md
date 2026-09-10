## ADDED Requirements

### Requirement: CPA and spool retention migration convergence

The migration graph SHALL have a single head after CPA catalog discovery and dashboard spool retention are combined. Upgrades from either previous branch head SHALL apply the missing branch without rewriting existing revision identities. Existing model-source identities and already-applied branch settings SHALL remain intact.

#### Scenario: Upgrade an existing CPA database

- **WHEN** a database at the CPA catalog revision upgrades to the unified head
- **THEN** the spool retention column SHALL become available
- **AND** existing catalog configuration and model identities SHALL remain intact

#### Scenario: Upgrade an existing spool retention database

- **WHEN** a database at the dashboard spool retention revision upgrades to the unified head
- **THEN** the CPA catalog columns SHALL become available
- **AND** the existing spool retention setting and model identities SHALL remain intact

#### Scenario: Step back across the merge revision

- **WHEN** an operator downgrades from the merge revision to either immediate parent
- **THEN** only the revision stamps SHALL change to the two parent heads
- **AND** both parent schemas and their data SHALL remain intact for a subsequent upgrade to the unified head
