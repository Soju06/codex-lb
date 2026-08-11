## Why

The dashboard overview error rate counts `status=cancelled` /
`error_code=client_disconnected` request logs as errors. Cancelled rows are
normal Codex CLI client lifecycle (a client disconnecting before the final SSE
event lands), not upstream failures — routing health already treats them as
non-penalizing. On multi-agent instances cancelled rows dominate the totals,
inflating a ~0% real upstream error rate to 60–98% and making the metric
useless for operational monitoring (issue #1552).

The `status != 'success'` fold is materialized in several places: the usage
metrics builders, the hourly time-rollup `error_count` measure, the reports
daily/summary aggregates, the fleet pressure metrics, and the `top_error`
computation (where `client_disconnected` dominates). Fixing only the builder
would leave the trend, aggregate, reports, and fleet paths inflated.

## What Changes

Per the maintainer-decided direction on #1552 (full status breakdown going
forward, not a numerator-only patch):

- Classify only `status NOT IN ('success', 'cancelled')` rows as errors
  everywhere the error fold is materialized: usage builders, the hourly
  rollup fold, the raw-tail bucket/activity/summary aggregates, the reports
  daily/summary aggregates, and the fleet pressure metrics. The error-rate
  denominator stays total requests.
- Exclude cancelled rows from `top_error` derivation; exclude the
  `client_disconnected` code read-side from the folded error satellite
  (historical satellite rows were folded under the old filter).
- Add a `cancelled_count` measure to `request_usage_hourly_rollups` via an
  additive Alembic migration on the current single head, folded as
  `sum(status = 'cancelled')` going forward.
- Surface cancelled counts in the dashboard overview metrics, the usage
  summary metrics, the reports daily/summary rows, and the fleet pressure
  metrics so dashboards can show success / cancelled / error distinctly.
- Source the dashboard-overview cancelled total from the demand quarter
  rollup (which preserves the full status grain, PR #1615) plus the raw
  tail, so the breakdown is accurate across already-folded history and
  consistent with the request-log listing counts.

### Historical-row compatibility (decided: no backfill)

Hourly rollup rows folded before this change keep the old
`sum(status != 'success')` error fold: they cannot be re-split without
evidence (raw rows may already be retention-pruned), so error-rate trends
show a disclosed step change at deploy — the same trade accepted for the
#1602 attribution fix. New folds write `error_count` excluding cancelled and
populate `cancelled_count`; pre-existing rows read `cancelled_count = 0`
via the column's server default. The dashboard-overview cancelled total does
not suffer this because it is sourced from the demand grain, which has
carried `status` as a dimension across all folded history.

## Capabilities

### New Capabilities

- `usage-error-metrics`

### Modified Capabilities

(none)

## Impact

- **Code:** `app/modules/usage/builders.py`,
  `app/modules/accounts/usage_time_rollup.py`,
  `app/modules/request_logs/repository.py`,
  `app/modules/reports/repository.py` (+ service/schemas),
  `app/modules/fleet/observability.py` (+ schemas),
  `app/modules/dashboard/builders.py` (+ schemas),
  `app/core/usage/logs.py`, `app/core/usage/types.py`, `app/db/models.py`.
- **DB:** additive migration adding
  `request_usage_hourly_rollups.cancelled_count` (server default 0);
  downgrade drops the column.
- **API:** additive fields only — `cancelledCount` on dashboard overview
  metrics and fleet pressure metrics, `cancelled7d` on usage summary
  metrics, `cancelled_count` / `total_cancelled` on report rows. Existing
  fields keep their names; `errorRate` / `errorCount` semantics narrow to
  genuinely-failed terminals.
- **Compatibility:** historical hourly rollup buckets keep the old error
  fold (disclosed step change on error-rate trends at deploy); no backfill.
  Frontend consumption of the new fields is out of scope here (additive
  fields default safely).
