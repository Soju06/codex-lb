## ADDED Requirements

### Requirement: External database network egress matches the connection source

When bundled PostgreSQL is disabled and NetworkPolicy is enabled, the Helm chart MUST permit external PostgreSQL egress on the port selected by the database connection source. A direct `externalDatabase.url` MUST use its explicit port or PostgreSQL's default port 5432 when omitted. A chart-generated database URL MUST use `externalDatabase.port`, defaulting both URL and egress to 5432 when the operator does not override it. Bundled PostgreSQL egress MUST continue to target its chart-managed service on port 5432.

#### Scenario: Custom external database port is rendered consistently

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.port=6432`
- **THEN** the chart-generated database URL uses port 6432
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 6432

#### Scenario: External database port retains its default

- **WHEN** an operator disables bundled PostgreSQL and enables NetworkPolicy
  without overriding `externalDatabase.port`
- **THEN** the chart-generated database URL uses port 5432
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432

#### Scenario: Direct external database URL uses its explicit port

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.url` with port 6432
- **THEN** the chart-generated Secret retains the direct database URL
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 6432

#### Scenario: Direct external database URL without a port uses the PostgreSQL default

- **WHEN** an operator disables bundled PostgreSQL, enables NetworkPolicy, and
  sets `externalDatabase.url` without an explicit port
- **THEN** the chart-generated Secret retains the direct database URL
- **AND** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432

#### Scenario: Bundled PostgreSQL egress is unchanged

- **WHEN** bundled PostgreSQL and NetworkPolicy are enabled
- **THEN** the PostgreSQL egress rule targets the chart-managed PostgreSQL pods
- **AND** it permits TCP 5432
