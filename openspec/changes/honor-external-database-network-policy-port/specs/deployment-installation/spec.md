## ADDED Requirements

### Requirement: External database network egress uses the configured port

The Helm chart MUST permit external PostgreSQL egress on the same
`externalDatabase.port` used by the chart-generated database URL when bundled
PostgreSQL is disabled and NetworkPolicy is enabled. If the operator does not
override the external database port, both rendered values MUST default to 5432. Bundled
PostgreSQL egress MUST continue to target its chart-managed service on port
5432.

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

#### Scenario: Bundled PostgreSQL egress is unchanged

- **WHEN** bundled PostgreSQL and NetworkPolicy are enabled
- **THEN** the PostgreSQL egress rule targets the chart-managed PostgreSQL pods
- **AND** it permits TCP 5432
