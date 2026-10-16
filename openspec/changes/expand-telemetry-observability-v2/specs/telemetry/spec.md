## MODIFIED Requirements

### Requirement: Transmission cadence and failure isolation

The service SHALL transmit one heartbeat at startup and one per 24-hour interval thereafter.
On each tick, after the heartbeat, the service SHALL transmit completed-UTC-day aggregate bodies
that have not been acknowledged, newest first, at most seven bodies per tick. The service MUST
capture one UTC date per tick and restrict day discovery in SQL to the seven completed calendar
days `[today_utc - 7 days, today_utc)`. Days older than this window MUST be marked acknowledged
without constructing or transmitting their bodies, even when the window contains no traffic.
The watermark MUST advance through only contiguous successful populated days from the oldest
end of the window; a failed day MUST remain unacknowledged while it is within the window.
In a multi-replica deployment sharing a database, snapshot construction and transmission MUST
run only under the existing leader-election gate so at most one replica performs each tick.
Telemetry transmission failures MUST NOT affect proxy operation, MUST use a bounded timeout,
MUST NOT retry more than once per interval, and MUST log failures at debug level only.

#### Scenario: Non-leader replica skips telemetry work

- **WHEN** a telemetry tick runs in a process that does not hold the scheduler leader lease
- **THEN** that process neither builds a snapshot nor attempts a transmission

#### Scenario: Collection endpoint outage is invisible

- **WHEN** the telemetry endpoint is unreachable
- **THEN** proxy requests are unaffected, startup is not delayed beyond the bounded timeout,
  and no warning-or-higher log noise is produced

#### Scenario: Backfill is bounded after an outage

- **GIVEN** an instance that transmitted nothing for thirty days
- **WHEN** the next tick runs
- **THEN** at most seven completed-day bodies are transmitted and the older unacknowledged days
  are marked acknowledged without transmission

#### Scenario: Sparse and empty recent traffic do not extend backfill

- **WHEN** a tick has traffic yesterday and forty days ago, or only traffic forty days ago
- **THEN** no body is constructed or transmitted for the forty-day-old traffic
- **AND** dates before the calendar window are acknowledged even if no recent body is sent

#### Scenario: A failed in-window day remains eligible

- **WHEN** some completed-day transmissions fail while others succeed
- **THEN** the watermark covers out-of-window dates and only the contiguous successful
  populated dates before the oldest failed day

### Requirement: One-time consent dialog with exact payload preview

The dashboard MUST present a one-time consent dialog on first entry while consent is
`undecided`, and the dialog MUST display the combined heartbeat envelope plus one sample
completed-day body the instance would transmit. Preview and sender MUST use the same
constructors for both bodies, including one shared heartbeat envelope constructor. The
preview timestamp MUST record preview generation time as a representative current timestamp;
the actual send MUST regenerate that value at transmission time.

The consent notice carries a version. When the transmitted payload schema gains fields, the
notice version MUST be raised, and installations whose acknowledged notice version is lower MUST
be shown the current exact payload preview once. The version MUST be recorded only by an explicit
acknowledgement request after the preview has been shown; reading the preview MUST NOT acknowledge
it. That re-display MUST preserve any persisted consent decision rather than resetting consent to
`undecided`.

The consent surface MUST disclose the collector's per-instance detail retention duration.

A decision (enable or disable) MUST be persisted and the dialog MUST NOT be shown again after
any decision at the same notice version. The dialog MUST offer disabling with no fewer clicks
than enabling.

The consent API MUST build the preview only while the undecided dialog is eligible or when an
operator explicitly requests it for the settings view. The response MUST retain the `preview`
field and set it to `null` when the preview was not requested and is not dialog-relevant.

#### Scenario: Undecided operator sees payload preview

- **WHEN** an operator opens the dashboard while consent is `undecided`
- **THEN** a dialog shows the combined heartbeat envelope and one sample completed-day body,
  built by the same constructors the sender uses, with equally prominent enable and disable actions

#### Scenario: Dismissing without a decision keeps the undecided dialog reachable

- **GIVEN** default consent remains `undecided` and the current notice version is acknowledged
- **WHEN** the operator returns to the dashboard without having persisted a decision
- **THEN** the consent response still includes the exact payload preview for the undecided dialog

#### Scenario: Decision is final until changed in settings

- **WHEN** the operator chooses disable in the dialog
- **THEN** consent persists as `disabled`, no snapshot is transmitted afterward, and the
  dialog never reappears at that notice version

#### Scenario: Decided consent status is a cheap read

- **WHEN** the dashboard reads consent after a persisted decision without requesting a preview
- **THEN** the response contains `preview: null` and no snapshot aggregation query runs

#### Scenario: Settings explicitly requests collected data

- **WHEN** the settings view requests a preview for any consent state
- **THEN** the response contains the combined current heartbeat envelope and one sample completed-day
  body, both built by the same constructors the sender uses

#### Scenario: Schema expansion re-informs a decided operator

- **GIVEN** an installation with persisted `enabled` consent acknowledged at an earlier notice version
- **WHEN** the service upgrades to a version whose notice version is higher
- **THEN** the operator is shown the current exact payload preview once, the persisted `enabled`
  decision is retained, and the notice is not shown again after acknowledgement

#### Scenario: Dropped preview response is not acknowledged

- **GIVEN** an installation with a decided consent and an older acknowledged notice version
- **WHEN** the preview response is dropped before the explicit acknowledgement request
- **THEN** the next GET still returns the current exact payload preview

#### Scenario: Consent surface states the retention duration

- **WHEN** the consent dialog or the settings telemetry view is rendered
- **THEN** it states how long the collector retains per-instance detail

## ADDED Requirements

### Requirement: Exact account counts with spend-correlated aggregates still bucketed

API key count, database size, and cost aggregates MUST be transmitted as documented buckets, never as exact values.

Account pool size, per-plan account counts, and per-status account counts MUST be transmitted as exact integers counting locally configured account rows. These counts MUST NOT be presented, on any collector or public surface, as unique upstream accounts or as unique users, because the same upstream account configured in two installations is counted twice.

An unmeasurable database size MUST be reported as `unknown`, not as a plausible size bucket.

#### Scenario: Pool size is exact

- **WHEN** an instance has 13 linked accounts
- **THEN** the payload reports an exact account total of 13 and no account count bucket

#### Scenario: Plan and status breakdowns are exact

- **WHEN** an instance has accounts across several plans and several account statuses
- **THEN** the payload reports an exact integer count per plan and per status, and their sums each equal the exact account total

#### Scenario: Spend-correlated aggregates stay bucketed

- **WHEN** the snapshot reports API key count, seven-day cost, and database size
- **THEN** each is a documented bucket string and no exact value for any of the three appears in the payload

#### Scenario: Unmeasurable database size is unknown

- **WHEN** the database size cannot be measured
- **THEN** `db_size_bucket` is `unknown` rather than any size bucket

### Requirement: Summable completed-day aggregates

Traffic statistics MUST be transmitted as aggregates over completed UTC days, identified by
`(instance_id, utc_date, schema_version)` so that redelivery of the same day replaces rather
than adds to previously received data. A day MUST NOT be transmitted until that UTC day has
ended.

Rolling-window traffic values MUST NOT be summed across snapshots. Any rolling-window value that
remains in the payload MUST be documented as instantaneous state that overlaps between
transmissions.

#### Scenario: Redelivered day does not double count

- **WHEN** the same completed UTC day is transmitted more than once for one instance
- **THEN** the collector stores one record for that `(instance_id, utc_date, schema_version)`
  and fleet totals for that day are unchanged by the redelivery

#### Scenario: Restart does not inflate traffic totals

- **GIVEN** an instance restarted several times within one day
- **WHEN** its transmissions for that period are aggregated
- **THEN** the completed-day traffic counted for that instance equals the traffic of the
  completed days themselves, independent of restart count

#### Scenario: The current partial day is not transmitted as a day aggregate

- **WHEN** a tick runs partway through a UTC day
- **THEN** no day aggregate is transmitted for the in-progress day

### Requirement: Mergeable fixed-bucket performance histograms

Latency, time-to-first-token, and tokens-per-second distributions MUST be transmitted as counts
over fixed, hard-coded logarithmic bucket edges defined in this capability's `context.md`, never
as instance-computed percentiles alone. Bucket edges MUST NOT be configurable per installation.

Each histogram MUST carry a `sample_count` equal to the sum of its bucket counts. Buckets with
a zero count MAY be omitted.

TPS samples MUST require both measured latency and measured time-to-first-token, using the
reports definition `(output_tokens - coalesce(reasoning_tokens, 0)) * 1000 /
(latency_ms - latency_first_token_ms)` with positive numerator and denominator. A measured
time-to-first-token of zero MUST remain eligible; a NULL measurement MUST NOT produce a sample.

Percentiles reported by any collector or public surface MUST be computed by summing bucket counts
across contributing instances. A percentile MUST NOT be derived by averaging or taking a
percentile of per-instance percentiles.

#### Scenario: Fleet percentiles are computed from summed buckets

- **WHEN** several instances report histograms for the same completed day
- **THEN** the fleet percentile for that day is computed from the summed bucket counts

#### Scenario: Sample count matches the buckets

- **WHEN** a histogram is serialized
- **THEN** its `sample_count` equals the sum of its bucket counts

#### Scenario: Missing measurements cannot fabricate throughput

- **WHEN** latency or TTFT is NULL, or latency does not exceed TTFT
- **THEN** the row contributes no TPS sample
- **AND** a measured TTFT of zero with positive latency and net output tokens contributes a sample

#### Scenario: Bucket edges are identical across installations

- **WHEN** two installations of different versions report the same metric
- **THEN** their histograms use the same bucket edges and are summable without rescaling

### Requirement: Exact per-dimension counts without cross-dimension cells

Traffic statistics MUST report exact request counts per allowlisted dimension value for model,
client family, transport, upstream transport, service tier, and request kind. Each dimension MUST
be reported as an independent marginal of the same day.

The payload MUST NOT contain any statistic keyed by two or more dimensions simultaneously, and
the outbound wire-schema test MUST reject a body containing such a cell.

Dimension values MUST come from the existing allowlists, with unmatched values folded into
`other`. Both heartbeat and day model names MUST use the application's bundled bootstrap slug
allowlist, independent of operator configuration or upstream catalog discovery. Persisted upstream
transport `websocket` MUST map to `ws`; `openai_compatible_http` and `http` MUST map to `http`.
Unmatched upstream transport values MUST map to `other`.

Persisted request transport `websocket` MUST map to `ws`, `http` MUST map to `http_bridge`,
and all other values MUST map to `other`.

Day discovery and aggregation MUST use the reports normal-traffic predicate, excluding rows with
source `limit_warmup` or request kind `warmup` or `limit_warmup`.

#### Scenario: Dimension totals are exact

- **WHEN** a completed-day aggregate reports per-model and per-client statistics
- **THEN** each entry carries an exact request count rather than only a share

#### Scenario: Cross-dimension cells are rejected

- **WHEN** a payload is serialized containing a statistic keyed by both model and client family
- **THEN** the outbound wire-schema test fails

#### Scenario: Unlisted dimension values fold to other

- **WHEN** traffic uses a model or client value absent from the allowlist
- **THEN** its counts are attributed to `other` and the unmatched raw value is absent from the payload

#### Scenario: Live catalog discovery cannot disclose private models

- **WHEN** an upstream catalog advertises a private model alongside a bundled bootstrap model
- **THEN** both serialized outbound bodies preserve the bootstrap model name and replace the
  private name with `other`

#### Scenario: Warmups do not populate a day aggregate

- **WHEN** a day contains normal requests, warmup requests, and limit-warmup requests
- **THEN** its day aggregate counts the same normal requests as the reports aggregate
- **AND** a warmup-only day is absent from day discovery

### Requirement: Structured error taxonomy without free text

Error statistics MUST report exact counts for upstream error class, internal failure phase, and
HTTP status class as three independent count maps, plus exact success, error, and cancelled
totals. Values outside the documented enumerations MUST be reported as `other`.

Persisted failure phases MUST map `usage_settlement` to `settle`, `upstream` to `stream`, and
`bridge` to `bridge_queue`; unrecognized phases MUST remain `other`.

Free-text error messages, failure details, exception type names, and bridge stage strings MUST
NOT be transmitted.

#### Scenario: Upstream incident is sizable from telemetry

- **WHEN** an upstream error class occurs during a completed day
- **THEN** that class carries an exact count rather than only appearing in a truncated name list

#### Scenario: Rate limiting is separable from other client errors

- **WHEN** the HTTP status class map is reported
- **THEN** `429` is counted separately from other `4xx` responses

#### Scenario: Free-text failure data never leaves the instance

- **WHEN** request logs contain error messages, failure details, and exception type names
- **THEN** none of those strings appear in any transmitted body

### Requirement: Collector retention and exposure tiers

The project-operated collector MUST retain per-instance detail for no longer than 365 days, after
which per-instance detail MUST be deleted. Fleet aggregates that carry no instance identifier MAY
be retained beyond that period.

Per-instance detail MUST be served only to authenticated administrative access. Public surfaces
MUST serve only fleet aggregates, and MUST omit any aggregate cell whose contributing-instance
count is below the documented minimum threshold.

Registration counts, `active_7d`, and any similar liveness count MUST NOT be presented as unique
users on any surface.

#### Scenario: Detail expires at the retention boundary

- **WHEN** per-instance detail becomes older than 365 days
- **THEN** it is deleted from the collector while non-identifying fleet aggregates for that period remain available

#### Scenario: Public surface withholds sparse cells

- **WHEN** a fleet aggregate cell has fewer contributing instances than the documented threshold
- **THEN** that cell is absent from public responses

#### Scenario: Per-instance detail requires administrative authentication

- **WHEN** an unauthenticated caller requests per-instance detail
- **THEN** the request is refused

## REMOVED Requirements

### Requirement: Bucketed sensitive aggregates

Account pool size, per-plan account counts, API key count, database size, and cost aggregates MUST be transmitted as documented buckets, never as exact values.

An unmeasurable database size MUST be reported as `unknown`, not as a plausible size bucket.

#### Scenario: Pool size is a bucket

- **WHEN** an instance has 13 linked accounts
- **THEN** the payload reports the `6-20` bucket and no exact account count
