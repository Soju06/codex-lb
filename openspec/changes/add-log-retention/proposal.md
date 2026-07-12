## Why

`request_logs`, `usage_history`, and `additional_usage_history` grow without bound — the only deletion anywhere is per-account cleanup. Unbounded growth is what turns every windowed dashboard aggregate, facet query, and backup slowly worse forever (usage_history alone adds ~1,440 rows/day/account/window). With account usage summaries now served from a persistent rollup (`add-account-usage-rollup`), old request-log rows can finally be pruned without corrupting lifetime totals.

## What Changes

- Add opt-in retention settings (env): `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` and `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` (the latter covers both usage-history tables). `0` (default) disables retention entirely — no behavior change unless an operator opts in. Non-zero values are validated against safe minimums (request logs ≥ 30 days; usage history ≥ 45 days, exceeding the monthly window).
- Add a leader-gated background retention job that hard-deletes, in bounded batches:
  - `request_logs` rows older than the cutoff **and** at or below the account-usage-rollup watermark — unfolded rows are never pruned, so lifetime account totals survive pruning by construction. If the rollup watermark does not exist yet, request-log pruning is skipped.
  - `usage_history` / `additional_usage_history` rows older than the cutoff, **always retaining the latest row per (account, window[, quota_key])** so paused/idle accounts keep their last-known usage on the dashboard.
- Fold per-API-key lifetime sums into a new `api_key_usage_rollups` table in the same fold pass (adversarial-review finding: the dashboard's per-key lifetime totals aggregate `request_logs` unbounded, so pruning would silently erode them). API-key summaries read rollup + live tail, preserving their exact semantics; key deletion drops the rollup row. This also removes another unbounded dashboard scan.
- Invalidate the SQLite bulk-history cache after usage-history pruning.
- Document the operator contract: pruning truncates historical reports/log listings older than the retention window; `previous_response_id` owner lookups only cover retained logs. Folded per-key sums intentionally persist when an account's history is hard-deleted.

## Capabilities

### New Capabilities

- `data-retention`: retention configuration, pruning safety invariants (rollup-watermark gate, latest-usage-row preservation), batching, and scheduler behavior.

### Modified Capabilities

- `query-caching`: API-key usage summaries MUST be served from a persistent rollup plus a bounded live tail (same fold watermark as account summaries), with rollup rows following the key lifecycle.

## Impact

- **Code**: new `app/core/retention/` job + scheduler, `app/core/config/settings.py` fields, `app/main.py` lifespan wiring, repositories' delete helpers.
- **Schema**: none (no migration).
- **Ops**: one new periodic job (hourly), leader-only; disabled by default.
- **Compatibility**: default-off; enabling changes only how far back historical data reaches, which is the feature's purpose.
