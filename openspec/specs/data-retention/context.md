## Overview

Retention bounds the growth of `request_logs`, `usage_history`, and `additional_usage_history`. It is **disabled by default**; operators opt in from the dashboard (Settings → Advanced → Data retention):

- request logs — 0 (off) or ≥ 30 days
- usage history — 0 (off) or ≥ 45 days (covers both usage-history tables)

An hourly, leader-gated background job deletes aged rows in 10,000-row batches, one transaction per batch.

## Configuration precedence and override semantics

Retention is a per-deployment policy the operator may want to tighten or relax while watching disk usage — a restart-requiring env var is the wrong shape for it (PRINCIPLES.md P2), so it lives in dashboard runtime settings (`dashboard_settings` row + `SettingsCache` with cross-replica invalidation) like every comparable policy. Per window, the effective value resolves as:

1. dashboard value (non-NULL, including `0` = explicitly disabled)
2. disabled (`0`) — `NULL` means "never set from the dashboard"

The dashboard API enforces the safety floors (0 or ≥ 30 request logs / 0 or ≥ 45 usage history, max 3650) so in-product consumer windows stay inside retained data.

The GET settings API returns both the *effective* value per window (`requestLogRetentionDays`) and the raw nullable stored value (`requestLogRetentionOverrideDays`, `null` = not configured). The `*Override*` names are kept for API compatibility from the release in which the stored value overrode a deprecated env alias. Updates use only the override fields, tri-state: absent = unchanged, present `null` = clear back to NULL, present value = store. Full-save clients echo the override fields verbatim, so `null` round-trips as `null` and no echo heuristic is needed; the dashboard card additionally submits only the fields the operator edited and clears a stored value by emptying the input.

## Scheduler behavior

The scheduler always starts and re-resolves the effective retention at the top of each hourly tick (a single SettingsCache-backed read); when the effective configuration is disabled the tick returns before leader election. Leader-election gating of the actual pass (`run_if_leader`, heartbeat-renewed) is unchanged. A dashboard change therefore takes effect within one tick on every replica, without restart. (Previously the scheduler computed `enabled` once at startup from env settings and did not start at all when disabled.)

## Env-alias retirement (done)

- `retention-dashboard-settings` (PR #1364, v1.21.x) moved retention to the dashboard and kept `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` as deprecated aliases for NULL dashboard values.
- `remove-dead-env-settings` (first release after v1.24.0; v1.22–v1.24 shipped the deprecation window) removed the env fields. The two names are in `_REMOVED_SETTINGS`, so an operator who still sets them gets the one-release startup warning; the effective window for a NULL dashboard value is now `0` (disabled), so such an operator must set the window from the dashboard once.

## Decisions

- **Floors**: 30 days keeps default report ranges and `previous_response_id` owner lookups inside retained data; 45 days exceeds the monthly usage window (~31 days) plus margin. Sub-floor non-zero values are rejected with a validation error by the dashboard API.
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

An operator upgrading with `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` still in `.env.local` sees the startup warning naming it, opens Settings → Advanced → Data retention (effective value 0 = disabled, input empty), and stores 30. Within one scheduler tick the leader prunes request logs older than 30 days — no restart. With a fold watermark at `now − 24h`, a row requested 31 days ago is deleted (older than cutoff, below watermark), while a row requested 2 hours ago is kept at any retention setting (above the watermark, unfolded).
