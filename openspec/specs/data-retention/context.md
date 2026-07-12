## Overview

Retention bounds the growth of `request_logs`, `usage_history`, and `additional_usage_history`. It is **disabled by default**; operators opt in with env settings:

- `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` — 0 (off) or ≥ 30
- `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` — 0 (off) or ≥ 45 (covers both usage-history tables)

An hourly, leader-gated background job deletes aged rows in 10,000-row batches, one transaction per batch.

## Decisions

- **Floors**: 30 days keeps default report ranges and `previous_response_id` owner lookups inside retained data; 45 days exceeds the monthly usage window (~31 days) plus margin. Sub-floor non-zero values fail settings validation at startup.
- **Rollup gate**: request-log pruning deletes only rows at or below the account-usage-rollup watermark (`min(cutoff, folded_through)`), so lifetime account totals survive pruning by construction; with no watermark (fold never ran) request-log pruning is skipped entirely.
- **Latest-row preservation**: usage-history pruning always retains the newest row per `(account_id, coalesce(window,'primary'))` and per `(account_id, quota_key, window)` so idle or paused accounts keep their last-known usage on the dashboard, regardless of age.
- **No partitioning**: batched deletes are sufficient at codex-lb volumes and avoid a heavyweight migration; revisit if tables reach hundreds of millions of rows.

## Operational Notes

- First pass after opt-in drains the historical backlog incrementally across hourly runs (10k rows per transaction); no long-lived locks.
- Enabling retention truncates how far back the request-log page, reports, and `earliest_activity_at` reach — that is the feature's purpose, not data loss.
- Resumed conversations whose `previous_response_id` predates the retention window can no longer resolve a pinned owner account; the ≥ 30-day floor makes this practically unreachable.
- On SQLite, the projections bulk-history cache is invalidated after usage-history pruning.
- Per-API-key lifetime totals are folded into `api_key_usage_rollups` under the same watermark, so pruning never erodes them. Folded key sums intentionally persist when an account is deleted with `delete_history=True` (the legacy live aggregate would have shrunk).
- Protected latest-row id sets are computed once per pass (not per batch); retention settings are capped at 3650 days.

## Example

With `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` and a fold watermark at `now − 24h`, a row requested 91 days ago is deleted (older than cutoff, below watermark), while a row requested 2 hours ago is kept even at any retention setting (above the watermark, unfolded).
