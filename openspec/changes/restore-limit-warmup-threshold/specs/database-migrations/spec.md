## MODIFIED Requirements

### Requirement: Limit warm-up persistence

The database SHALL retain the legacy `limit_warmup_exhausted_threshold_percent`
column and SHALL include the active
`limit_warmup_reset_threshold_percent` Float column. The activation migration
MUST depend on the compatibility migration, initialize every existing active
value to `0.0`, and make the active column non-null with a `0.0` server default.
The legacy column and its stored values MUST remain untouched. The current ORM
MUST read and write only the active column for the public setting; the legacy
column may remain internally mapped solely to prevent schema drift.

#### Scenario: Existing rows receive the active default

- **GIVEN** an existing dashboard settings row has a `NULL` active value after
  the compatibility migration
- **WHEN** the activation migration is applied
- **THEN** the active value becomes `0.0`
- **AND** the active column is non-null with a `0.0` server default
- **AND** the legacy value remains unchanged

#### Scenario: Explicit legacy values are not copied into active storage

- **GIVEN** an existing row has a legacy threshold of `50.0` or `99.0`
- **WHEN** the activation migration is applied
- **THEN** the active value is `0.0`
- **AND** the legacy value remains its original value

#### Scenario: Runtime writes use active storage only

- **WHEN** the current settings API writes a threshold of `37.5` or `0.0`
- **THEN** the active column stores that exact value
- **AND** the legacy column is not synchronized by application code

#### Scenario: Downgrade returns the compatibility shape

- **GIVEN** the active column was activated by this change
- **WHEN** the activation migration is downgraded
- **THEN** the active column remains present but becomes nullable with no server default
- **AND** its stored value is preserved
- **AND** the legacy column and its stored value remain unchanged

#### Scenario: Warm-up attempt is unique per reset

- **WHEN** an attempt is stored for an account, window, and reset timestamp
- **THEN** the database enforces uniqueness for that account/window/reset tuple

#### Scenario: Existing installs remain disabled

- **WHEN** an existing database is migrated
- **THEN** global warm-up is disabled
- **AND** all existing accounts remain opted out

#### Scenario: Warm-up request logs remain separable from user traffic

- **WHEN** a warm-up request is logged
- **THEN** the request log records a source value that allows account usage
  summaries to exclude internal warm-up traffic
