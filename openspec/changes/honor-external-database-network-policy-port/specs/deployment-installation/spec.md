## ADDED Requirements

### Requirement: External database network egress matches the connection source

When bundled PostgreSQL is disabled and NetworkPolicy is enabled, the Helm chart MUST permit external PostgreSQL egress on every port selected by the database connection source. When `externalDatabase.url` is the active source, its authority port or supported SQLAlchemy query ports, including percent-encoded ASCII forms, MUST take precedence and render as unique decimal Kubernetes ports. Blank query items MUST be ignored, and portless query hosts including IPv6 literals MUST inherit the authority port before defaulting to 5432; a port outside 1 through 65535 MUST fail rendering. When an existing Secret or ExternalSecret is the active source, a stale direct URL MUST be ignored and egress MUST use `externalDatabase.port` because Helm cannot inspect the secret value. A chart-generated database URL MUST use `externalDatabase.port`, defaulting both URL and egress to 5432 when the operator does not override it. Bundled PostgreSQL egress MUST continue to target its chart-managed service on port 5432.

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

#### Scenario: Equivalent direct URL port forms are normalized

- **WHEN** an active direct database URL supplies its effective port through an
  authority with leading zeros, a URL-encoded query `port`, or a query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits the same
  decimal TCP port used by SQLAlchemy

#### Scenario: Portless query host inherits the authority port

- **WHEN** an active direct database URL supplies an authority port and a
  portless query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits the
  authority TCP port used by SQLAlchemy

#### Scenario: Portless IPv6 query host keeps the PostgreSQL default

- **WHEN** an active direct database URL without an authority port supplies a
  portless IPv6 query `host`
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits TCP 5432
- **AND** no IPv6 hextet is interpreted as a port

#### Scenario: Blank query items do not override effective ports

- **WHEN** an active direct database URL contains blank `host` or `port` query
  items beside a valid port source
- **THEN** the blank items are ignored
- **AND** the external PostgreSQL NetworkPolicy permits only the effective port

#### Scenario: Multihost direct URL permits every failover port

- **WHEN** an active direct database URL supplies multiple query hosts on
  different valid ports
- **THEN** the external PostgreSQL NetworkPolicy egress rule permits every
  unique TCP port used by those hosts

#### Scenario: Secret-backed database source ignores a stale direct URL

- **WHEN** an existing Secret or ExternalSecret supplies the database URL
- **AND** an inactive direct URL declares a different port
- **THEN** the external PostgreSQL NetworkPolicy ignores the inactive URL
- **AND** its egress rule uses `externalDatabase.port`

#### Scenario: Invalid direct URL port fails rendering

- **WHEN** an active direct database URL declares a port outside 1 through 65535
- **THEN** Helm rendering fails before resources are applied

#### Scenario: Bundled PostgreSQL egress is unchanged

- **WHEN** bundled PostgreSQL and NetworkPolicy are enabled
- **THEN** the PostgreSQL egress rule targets the chart-managed PostgreSQL pods
- **AND** it permits TCP 5432
