## ADDED Requirements

### Requirement: Historical usage-limit stamps are reconciled against installed schema

Upgrade tooling MUST support the historical scalar, override, and merge usage-limit schemas preceding the linear migration graph as well as the same revision stamps produced by that linear graph. It MUST distinguish these states using installed schema, apply missing upstream changes, and preserve saved policies, account credentials, and unrelated data. It MUST preserve credential retirement when already applied. An unrecognized historical combination MUST fail clearly rather than silently declare an incomplete schema upgraded. A recognized canonical table, column, and credential state MUST retain ordinary index-drift handling.

#### Scenario: Historical scalar schema lacks upstream dashboard tables

- **WHEN** a historical scalar or override schema upgrades under the linear graph
- **THEN** the complete upstream schema and usage-limit columns are installed with existing values preserved

#### Scenario: Historical first merge lacks override columns

- **WHEN** a historical first-merge database upgrades
- **THEN** both override columns are installed and existing account data is preserved

#### Scenario: Reparented schema has the same stamp

- **WHEN** a database produced by the linear graph upgrades with the same scalar or override stamp
- **THEN** already-applied upstream changes are not destructively replayed

#### Scenario: Canonical schema has ordinary index drift

- **WHEN** a canonical usage-limit schema is missing an index but retains its upstream table, column, and credential state
- **THEN** upgrade tooling uses the ordinary migration ledger and reports index drift through the existing checks without treating the database as an unknown historical installation
