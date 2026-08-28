## Overview

Retention bounds the growth of `request_logs`, `usage_history`, and `additional_usage_history`. It is **disabled by default**; operators opt in from the dashboard (Settings → Advanced → Data retention), with deprecated env aliases still honored for one release:

- request logs — 0 (off) or ≥ 30 days
- usage history — 0 (off) or ≥ 45 days (covers both usage-history tables)

An hourly, leader-gated background job deletes aged rows in 10,000-row batches, one transaction per batch.

## Configuration precedence and override semantics

Retention is a per-deployment policy the operator may want to tighten or relax while watching disk usage — a restart-requiring env var is the wrong shape for it (PRINCIPLES.md P2), so it lives in dashboard runtime settings (`dashboard_settings` row + `SettingsCache` with cross-replica invalidation) like every comparable policy. Per window, the effective value resolves as:

1. dashboard value (non-NULL, including `0` = explicitly disabled)
2. env alias (`CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`), **deprecated**
3. disabled (`0`)

`NULL` in the dashboard column means "never set from the dashboard", which keeps existing env-configured deployments working unchanged through the deprecation window. The dashboard API mirrors the env safety floors exactly (0 or ≥ 30 request logs / 0 or ≥ 45 usage history, max 3650) with the same error wording, so in-product consumer windows stay inside retained data regardless of which layer configured retention.

The GET settings API returns both the *effective* value per window (`requestLogRetentionDays`, dashboard override falling back to the env alias — same convention as the `proxy_account_*` concurrency caps) and the raw nullable *override* (`requestLogRetentionOverrideDays`, `null` = inherit). Updates use only the override fields, tri-state: absent = unchanged, present `null` = clear back to inherit, present value = store — including a value equal to the env alias, which deliberately pins it as a dashboard override (a codex review P2 showed a value-based echo guard made that capture impossible). Full-save clients echo the override fields verbatim, so `null` round-trips as `null` and no echo heuristic is needed; the dashboard card additionally submits only the fields the operator edited and clears an override by emptying the input.

## Scheduler behavior

The scheduler always starts and re-resolves the effective retention at the top of each hourly tick (a single SettingsCache-backed read); when the effective configuration is disabled the tick returns before leader election. Leader-election gating of the actual pass (`run_if_leader`, heartbeat-renewed) is unchanged. A dashboard change therefore takes effect within one tick on every replica, without restart. (Previously the scheduler computed `enabled` once at startup from env settings and did not start at all when disabled.)

## Env-alias deprecation plan (PENDING follow-up)

- Shipped release (`retention-dashboard-settings`, PR #1364): the env vars keep working as aliases; their comment in `app/core/config/settings.py` marks them deprecated. They are deliberately NOT in `_REMOVED_SETTINGS`.
- **Pending later phase**: remove the env fields and add `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` to `_REMOVED_SETTINGS` with a pointer to the dashboard setting, once operators have had a release to migrate. Also tracked in the next-release queue in `openspec/specs/deployment-installation/context.md`.

## Decisions

- **Floors**: 30 days keeps default report ranges and `previous_response_id` owner lookups inside retained data; 45 days exceeds the monthly usage window (~31 days) plus margin. Sub-floor non-zero values fail settings validation at startup (env) or are rejected with a validation error (dashboard API).
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

An operator running with `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` opens Settings → Advanced → Data retention, sees 90 prefilled (effective value), and changes it to 30. The row now stores 30; within one scheduler tick the leader prunes request logs older than 30 days — no restart, and the stale env var no longer matters. With a fold watermark at `now − 24h`, a row requested 31 days ago is deleted (older than cutoff, below watermark), while a row requested 2 hours ago is kept at any retention setting (above the watermark, unfolded).
