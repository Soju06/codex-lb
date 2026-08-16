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
- Normalize the model-source streaming producer: a downstream disconnect
  mid-stream (`CancelledError`/`GeneratorExit`) is recorded as
  `status='cancelled'` (matching the main proxy streaming path) instead of
  `status='error'`, so the status-driven numerators classify it correctly.
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

Rolling-upgrade fence: the migration runs before old replicas drain, so a
legacy leader can still fold post-migration hours with the old error fold
and advance the shared watermark — up to `TS_MAX_SLICES_PER_PASS x
TS_FOLD_SLICE` per pass when a backfill is behind, so no fixed trailing
window can bound the damage. Old writers run old code and cannot be fenced,
so the suspect range is persisted instead: the migration adds
`account_usage_rollup_state.upgrade_repair_from`, stamping existing rows
with their migration-time `hourly_folded_through`; the column's epoch server
default marks a state row bootstrapped by an OLD replica after the migration
(its whole backfill is legacy-folded), while new code's own bootstrap writes
NULL — a value only new code ever writes. New code refolds
`[upgrade_repair_from, hourly_folded_through)` from raw in slice-sized
chunks (progress persists through the marker; a crash resumes; a
pass-bounded incomplete repair continues next pass) and clears the marker
when done. With the marker NULL, each process's first fold pass still
refolds the trailing `UPGRADE_REPAIR_WINDOW` (48h — one backfill slice,
comfortably longer than any rollout) as flip-flop defense: an old replica
that regains fold leadership after the marker was cleared writes legacy
buckets the marker no longer tracks, and the rollout that makes this
possible guarantees another new-code process starts afterwards. Both paths
clamp to hours fully covered by surviving raw rows (retention prunes
oldest-first with a contiguous frontier, so the clamp is exact), run
idempotent DELETE-then-INSERT recomputation under the existing leader gate
and fold-state row lock, and never move the watermark; buckets below the
clamp — including all pre-deployment history — keep the disclosed legacy
fold. This is a targeted repair of the rollout window, not a historical
backfill.

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
  `request_usage_hourly_rollups.cancelled_count` (server default 0) and
  `account_usage_rollup_state.upgrade_repair_from` (nullable, epoch server
  default, stamped to the migration-time watermark on existing rows);
  downgrade drops both columns.
- **API:** additive fields only — `cancelledCount` on dashboard overview
  metrics and fleet pressure metrics, `cancelled7d` on usage summary
  metrics, `cancelled_count` / `total_cancelled` on report rows. Existing
  fields keep their names; `errorRate` / `errorCount` semantics narrow to
  genuinely-failed terminals.
- **Compatibility:** historical hourly rollup buckets keep the old error
  fold (disclosed step change on error-rate trends at deploy); no backfill.
  Frontend consumption of the new fields is out of scope here (additive
  fields default safely).
