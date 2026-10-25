## ADDED Requirements

### Requirement: Background-owned persistent dashboard read snapshots
Dashboard overview and projections SHALL read complete server-side snapshots.
HTTP requests SHALL NOT aggregate quota/request history, simulate runway, or
start a refresh computation on cache miss or expiry. Background publication
SHALL atomically retain all dashboard range values and their evaluation clock.
Completed snapshots SHALL survive browser refresh and application replica restart.

#### Scenario: A browser reloads a dashboard range
- **GIVEN** a completed snapshot containing all dashboard ranges
- **WHEN** the browser reloads a selected range
- **THEN** the endpoint SHALL return that published value without starting measurement or aggregation
- **AND** cache state SHALL NOT be represented as insufficient quota samples

#### Scenario: A refresh fails or loses leadership
- **WHEN** a background refresh fails, is cancelled, or loses its lease
- **THEN** the previous complete value SHALL remain readable with truthful freshness
- **AND** an abandoned producer SHALL NOT overwrite a newer publication

#### Scenario: An older replica holds the metadata scheduler lease
- **WHEN** the new snapshot worker starts during an upgrade
- **THEN** it SHALL be able to acquire its independent refresh lease
- **AND** producers for the same snapshot capability SHALL remain serialized and fenced

#### Scenario: A snapshot is not yet available
- **WHEN** an authenticated request names an unwarmed valid key
- **THEN** it MAY register bounded background interest and SHALL report an explicit retryable warming state
- **AND** it SHALL NOT compute the missing value in the HTTP task or present fabricated zero data


### Requirement: Analytical traffic readiness is independent of inference readiness
A read-only analytical traffic-readiness check SHALL require current-schema
Dashboard and 7/30/90-day presets for UTC and admitted timezones. Values SHALL be
younger than their refresh interval plus 100 seconds and match the current local
report day. This check SHALL neither compute/register views nor transfer JSON
payloads. Deployment qualification SHALL require this check as well as ordinary
inference readiness, which snapshot failures SHALL NOT affect.

#### Scenario: A newly started replica has not prepared its reports
- **WHEN** inference infrastructure is ready but required snapshots are absent
- **THEN** ordinary inference readiness SHALL remain successful
- **AND** analytical traffic readiness SHALL return a retryable unavailable status

#### Scenario: Another replica already published current shared snapshots
- **WHEN** all required published generations are fresh and match the local dates
- **THEN** analytical traffic readiness SHALL succeed without recomputation

#### Scenario: A local report day rolls over
- **WHEN** the previous day's preset generation is still stored
- **THEN** analytical traffic readiness SHALL fail until the new day is published

### Requirement: The derived-table migration resumes interrupted DDL safely
The read-snapshot migration SHALL support retry after creating its table but
before completing its index or revision publication, and its downgrade SHALL
support retry after removing the derived table but before revision publication.
Unexpected existing columns, generated expressions, defaults, indexes or constraints SHALL fail closed,
including before a downgrade removes an existing table. Neither retry SHALL
modify existing accounting, quota, request-history, scheduler or bridge rows.

#### Scenario: A database stops the migration between table and index creation
- **WHEN** the same migration is retried at its parent revision
- **THEN** it SHALL preserve the existing derived-table rows and finish its index

#### Scenario: A cold old-version rollback stops after dropping the derived table
- **WHEN** the downgrade is retried with the new revision still recorded
- **THEN** it SHALL finish the downgrade without deleting or changing unrelated data

#### Scenario: An existing table has an unexpected constraint or default
- **WHEN** upgrade or downgrade encounters that table
- **THEN** it SHALL refuse before changing its contents, schema or revision

#### Scenario: A generated payload column imitates the expected storage type
- **WHEN** upgrade or downgrade encounters a computed payload column instead of writable snapshot storage
- **THEN** it SHALL refuse without stamping the revision or removing the unexpected table

#### Scenario: A scheduled refresh is still within its completion budget
- **GIVEN** a published snapshot reaches its scheduled refresh interval
- **WHEN** its age remains below the refresh interval plus 100 seconds
- **THEN** its freshness and traffic-readiness age checks SHALL remain successful
- **AND** exceeding that bound SHALL mark it stale and fail analytical readiness

#### Scenario: One producer lane encounters a transient database error
- **WHEN** a dashboard or report scheduling pass fails without cancellation
- **THEN** that lane SHALL log the failure and retry on a later tick
- **AND** the other lane SHALL remain running with its own session
- **AND** lease loss or shutdown SHALL still cancel and join both lanes
