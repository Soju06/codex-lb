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
Every request-log producer MUST record a downstream client disconnect as
`status='cancelled'`; in particular the model-source streaming path MUST NOT
record a mid-stream client disconnect as `status='error'`.

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

#### Scenario: Model-source stream disconnects land as cancelled

- **GIVEN** a streamed model-source request whose downstream client
  disconnects mid-stream
- **WHEN** the request log is written
- **THEN** the row has `status='cancelled'` and
  `error_code='client_disconnected'`
- **AND** the window's error count excludes it, its cancelled count includes
  it, and `top_error` does not report `client_disconnected`

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

### Requirement: New code repairs the rolling-upgrade fold window

Because the migration runs before old replicas drain, a legacy replica may
fold post-migration hours with the old error fold and advance the shared
watermark — by up to its full per-pass slice budget, so no fixed trailing
window can bound the damage. The migration MUST persist the legacy-suspect
range start on the fold-state row (`upgrade_repair_from`): existing rows are
stamped with their migration-time `hourly_folded_through`, and the column's
epoch server default covers a state row bootstrapped by an old replica after
the migration (its entire backfill is legacy-suspect); new code's own
bootstrap MUST write the marker as NULL, and NULL MUST only ever be written
by new code, meaning no legacy-suspect range is outstanding.

While the marker is set, the hourly fold pass MUST refold
`[upgrade_repair_from, hourly_folded_through)` from raw request logs in
bounded slice-sized chunks, persisting progress by advancing the marker with
each chunk's commit and setting it to NULL only once the range is covered —
a crash resumes instead of restarting, and a pass-bounded incomplete repair
continues on later passes. With the marker NULL, the first fold pass of each
new-code process MUST still refold the trailing repair window below the
watermark (a span that comfortably exceeds any rolling-upgrade duration) as
defense against a legacy replica regaining fold leadership after the marker
was cleared.

Both paths MUST be idempotent (converging DELETE-then-INSERT recomputation),
MUST run under the existing fold leader gate and fold-state row lock, MUST
NOT move the watermark, and MUST NOT touch folded buckets below the
surviving-raw clamp (whole hours fully covered by surviving raw rows;
retention-pruned history is irrecoverable and keeps the disclosed legacy
fold). This is a targeted repair of the rollout window only — not a
historical backfill.

#### Scenario: A legacy-folded post-migration bucket is repaired

- **GIVEN** a bucket inside the repair window whose rollup rows carry the
  legacy fold (cancelled rows in `error_count`, `cancelled_count=0`,
  `client_disconnected` in the error satellite) while its raw rows survive
- **WHEN** the new code runs its first hourly fold pass
- **THEN** the bucket is recomputed with `error_count` excluding cancelled
  rows, `cancelled_count` populated, and the `client_disconnected` satellite
  rows removed

#### Scenario: A multi-slice legacy advance is fully repaired via the marker

- **GIVEN** `upgrade_repair_from` set below legacy-folded buckets spanning
  more than one fold slice (a legacy leader advanced several slices in one
  pass)
- **WHEN** the new code runs its hourly fold passes
- **THEN** every bucket in `[upgrade_repair_from, watermark)` with surviving
  raw rows is recomputed and the marker ends NULL

#### Scenario: Buckets below the surviving-raw clamp are preserved

- **GIVEN** a folded bucket inside the repair span whose raw rows were
  already pruned by retention
- **WHEN** the repair runs
- **THEN** that bucket's rollup rows are left untouched

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
