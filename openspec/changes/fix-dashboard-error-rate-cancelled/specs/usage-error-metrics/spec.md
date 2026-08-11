## ADDED Requirements

### Requirement: Error metrics count only genuinely-failed terminals

Every materialization of a request-log error count or error rate — the usage
summary metrics, the dashboard overview activity metrics and per-bucket
error-rate trend inputs, the reports daily and summary aggregates, and the
fleet pressure metrics — MUST classify a request-log row as an error only
when `status NOT IN ('success', 'cancelled')`. Rows with `status =
'cancelled'` (normal client-side disconnect terminals, e.g.
`error_code=client_disconnected`) MUST NOT be counted in any error numerator.
Error-rate denominators MUST remain the total request count of the window.

#### Scenario: Cancelled rows do not inflate the dashboard error rate

- **GIVEN** a window containing 1 successful, 2 cancelled
  (`client_disconnected`), and 1 error (`upstream_500`) request-log rows
- **WHEN** the dashboard overview activity metrics are computed
- **THEN** the error count is `1` and the error rate is `0.25`
- **AND** the request total remains `4`

#### Scenario: Reports and fleet windows exclude cancelled rows from errors

- **GIVEN** the same window of rows
- **WHEN** the reports summary/daily aggregates and the fleet pressure
  metrics are computed
- **THEN** each reports `error_count` / `total_errors` and each fleet
  `error_count` equals `1`

### Requirement: Hourly rollups fold a cancelled_count measure

The `request_usage_hourly_rollups` table MUST carry a `cancelled_count`
measure (non-null, server default 0), introduced by an additive Alembic
migration whose parent is the current single migration head and whose
downgrade drops only the new column. The hourly fold MUST populate
`cancelled_count` as `sum(status = 'cancelled')` and MUST fold `error_count`
as `sum(status NOT IN ('success', 'cancelled'))`. Account lifecycle mirrors
MUST move `cancelled_count` with the other measures.

#### Scenario: Fold splits error and cancelled measures

- **GIVEN** one hour of raw rows with 1 success, 2 cancelled, and 1 error
  sharing the same dimensions
- **WHEN** the hourly fold pass folds that hour
- **THEN** the folded bucket has `request_count=4`, `error_count=1`, and
  `cancelled_count=2`

### Requirement: Historical hourly rollup rows keep the old error fold

Hourly rollup rows folded before the `cancelled_count` measure existed MUST
NOT be backfilled or re-split: their `error_count` keeps the legacy
`sum(status != 'success')` fold and their `cancelled_count` reads 0 via the
column's server default. Error-rate trends over such buckets exhibit a
disclosed step change at deploy.

#### Scenario: Pre-existing rollup rows are readable unchanged

- **GIVEN** a rollup row folded before the migration
- **WHEN** the dashboard reads it after upgrading
- **THEN** the read succeeds with `cancelled_count=0` and the row's stored
  `error_count` unchanged

### Requirement: Top error excludes cancelled terminals

`top_error` computations MUST NOT derive from cancelled rows: raw request-log
scans MUST filter `status NOT IN ('success', 'cancelled')`, the error
satellite fold MUST apply the same status filter going forward, and reads of
historical error-satellite rows (folded under the legacy filter) MUST exclude
the `client_disconnected` error code.

#### Scenario: client_disconnected no longer dominates top error

- **GIVEN** a window with 200 cancelled rows (`client_disconnected`) and 3
  error rows (`upstream_500`)
- **WHEN** `top_error` is computed for the dashboard or fleet windows
- **THEN** the result is `upstream_500`

### Requirement: Cancelled counts surface alongside error counts

Metric surfaces that expose an error count MUST also expose the window's
cancelled count as an additive field: the dashboard overview metrics
(`cancelledCount`), the usage summary metrics (`cancelled7d`), the reports
daily rows (`cancelled_count`) and summary (`total_cancelled`), and the fleet
pressure metrics (`cancelledCount`). The dashboard overview cancelled total
MUST be sourced from the demand quarter rollup (status grain) for the folded
segment plus the raw tail, so it stays accurate across history already folded
without the hourly `cancelled_count` measure.

#### Scenario: Dashboard overview reports the status breakdown

- **GIVEN** a window containing 1 successful, 2 cancelled, and 1 error rows
  that are partially folded into the rollups
- **WHEN** the dashboard overview metrics are computed
- **THEN** the metrics expose `requests=4`, `errorCount=1`, and
  `cancelledCount=2`
