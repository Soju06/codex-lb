## MODIFIED Requirements

### Requirement: Bounded report caching
Reports, options and diagnostic responses SHALL use shared, durable background
snapshots keyed by normalized dates, timezone and filters. The 7/30/90-day
presets SHALL be proactively refreshed about every five minutes for canonical
and known viewer timezones, independently of active browser requests. Expiry or
refresh failure SHALL retain the last good payload with freshness metadata.
HTTP reads SHALL NOT execute historical report aggregation or median SQL.
Custom variants MAY register bounded background interest without computing on
the request. Browser refresh SHALL retrieve prepared data rather than rebuild it.
Visible browser queries SHALL poll prepared results at most once a minute, or
every two seconds while cold preparation is pending, without retrying heavy
computation. Background tabs SHALL NOT run these polling timers.

#### Scenario: The operator opens each standard preset
- **GIVEN** completed background warmup for the viewer timezone
- **WHEN** the operator selects 7, 30 or 90 days
- **THEN** the response SHALL use its completed matching snapshot
- **AND** no synchronous history scan SHALL be performed by the request

#### Scenario: The worker refreshes overlapping windows
- **WHEN** the standard presets become due
- **THEN** work SHALL be bounded and reuse existing permanent aggregates
- **AND** optimized values SHALL preserve exact totals, filters, timezone boundaries and distinct-count semantics

#### Scenario: A worker failure occurs while reports are visible
- **WHEN** the next background refresh fails
- **THEN** the last complete report SHALL remain available and visibly stale
- **AND** selecting a different range SHALL NOT relabel the prior range as the requested result

#### Scenario: Concurrent identical reports
- **WHEN** concurrent requests use the same report filters
- **THEN** they SHALL read one published result without initiating aggregation
- **AND** at most one fenced background producer SHALL publish the next generation

#### Scenario: Cached result during an unrelated slow computation
- **GIVEN** a complete same-key result and an unrelated report computation in progress
- **WHEN** the cached report is requested
- **THEN** it SHALL return without waiting for the unrelated computation
- **AND** background jobs and registered interests SHALL remain bounded across replicas

#### Scenario: Failed computation
- **WHEN** a computation fails or is cancelled
- **THEN** the worker SHALL schedule an automatic retry without requiring a browser request
- **AND** HTTP reads SHALL return the prior complete same-key result with failure metadata, or an explicit unavailable state when no completed result exists

## ADDED Requirements

### Requirement: Retained observation history is disclosed
Report snapshots SHALL expose the earliest retained request activity matching
their filter scope, or null when no such activity exists. This SHALL identify
an observation boundary, not assert that the entire requested period was
measured continuously. Missing historical observations SHALL NOT be reconstructed.

#### Scenario: A ninety-day preset exceeds retained history
- **WHEN** a report covers dates before the earliest retained matching activity
- **THEN** its observation boundary SHALL be included in the prepared API result
- **AND** unsupported timing and thread metrics SHALL remain explicitly unavailable


### Requirement: Empty historical day prefixes do not expand consistent report queries
Within a database transaction providing a repeatable snapshot, report collection
MAY use the earliest retained matching activity to omit earlier empty SQL day
windows. This SHALL preserve the requested date range, timezone/DST boundaries,
all observed results, and the original requested-range timing availability guard.
Collectors without that consistency guarantee SHALL retain the full query range.

#### Scenario: A ninety-day request has seventeen days of retained observations
- **GIVEN** an earliest activity boundary read in the same repeatable transaction
- **WHEN** the daily rollup query is built
- **THEN** days wholly before that activity MAY be omitted from the SQL windows
- **AND** the returned report SHALL equal the untrimmed report, apart from its generation timestamp
- **AND** this optimization SHALL NOT enable unsupported ninety-day timing metrics

#### Scenario: An earlier historical row can become visible between statements
- **WHEN** the transaction uses read-committed or does not provide the required snapshot guarantee
- **THEN** the collector SHALL NOT assume that the previously read earliest timestamp bounds subsequent daily queries

### Requirement: Oversized read-model filters are rejected as client errors
An otherwise parsed report selection whose normalized snapshot parameters exceed
16 KiB SHALL return HTTP 400 with code `read_snapshot_filters_too_large`, without
registering the rejected report key or invoking a collector.

#### Scenario: A report or options request includes an oversized account selection
- **WHEN** the normalized filters exceed the snapshot admission bound
- **THEN** the endpoint SHALL return a bounded dashboard client-error envelope
- **AND** it SHALL NOT return HTTP 500 or enqueue the oversized view
