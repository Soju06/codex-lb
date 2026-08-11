## 1. Schema and migration

- [x] 1.1 Add `cancelled_count` to `RequestUsageHourlyRollup` and an additive
      Alembic migration on the current single head (downgrade drops it).
- [x] 1.2 Fold `cancelled_count` and the narrowed `error_count` in the hourly
      fold; apply the status filter to the error-satellite fold; carry the new
      measure through the lifecycle mirrors.

## 2. Error classification and surfaces

- [x] 2.1 Introduce shared status classification constants and use them in
      usage builders, request-log aggregates, reports, and fleet metrics.
- [x] 2.2 Exclude cancelled rows from `top_error` (raw paths) and exclude
      `client_disconnected` read-side from the folded error satellite.
- [x] 2.4 Record model-source stream disconnects as `status='cancelled'`
      (matching the main proxy path), with regression at the route.
- [x] 2.5 Repair the rolling-upgrade fold window: persisted
      `upgrade_repair_from` marker (migration-stamped; epoch default for
      old-code bootstraps; NULL only written by new code) driving a chunked,
      crash-resumable refold of the exact legacy-suspect range, plus a
      trailing-window flip-flop defense on each process's first fold pass
      (leader-gated, idempotent, clamped to surviving raw; no historical
      backfill).
- [x] 2.3 Surface cancelled counts: dashboard overview metrics (demand-grain
      sourced), usage summary metrics, reports daily/summary rows, fleet
      pressure metrics.

## 3. Tests and verification

- [x] 3.1 Regression coverage at the metric surfaces: builders, request-log
      aggregates, hourly fold, reports repository/service, fleet
      observability — mixed success/cancelled/error windows.
- [x] 3.2 Migration chain coverage (additive, single head).
- [x] 3.3 `uv run ruff format --check .`, `uv run ruff check .`, targeted
      `uv run pytest`, and strict OpenSpec validation.
