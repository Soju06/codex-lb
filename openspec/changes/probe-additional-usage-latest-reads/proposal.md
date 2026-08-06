## Why

The PostgreSQL latest-per-account read over `additional_usage_history`
(`AdditionalUsageRepository.latest_by_account`) still uses `DISTINCT ON`
over every row matching `(quota_key, window)`. The supporting index
(`ix_additional_usage_quota_window_latest`) makes that scan ordered, but not
small: at ~730k rows per quota key the read walks the whole key's history to
return one row per account (measured 0.7 s warm, 5–21 s under contention on
the reference deployment). It runs up to 8× per proxied request from the
load balancer's additional-limit filter, 2×N(quota keys) per
`GET /api/accounts` poll, and 2×N(limit names) per rate-limit-status
payload — the alias variant re-walks the same rows a second time. The
unfiltered distinct label listing (`list_quota_keys`) is a full index pass
for the same reason.

Cost scales with history length instead of account count, so it degrades
without bound as history grows (full-history retention is a supported
configuration).

## What Changes

- Replace the PostgreSQL `DISTINCT ON` shape in `latest_by_account` with
  correlated per-account top-1 probes (the same LATERAL shape
  `UsageRepository.latest_by_account` already uses): one btree descent per
  (account × canonical quota key value), plus one per (account × alias
  value) when the registry declares aliases. Result semantics are unchanged
  — newest `recorded_at`, then highest `used_percent`, then highest `id`,
  merged across canonical and alias matches exactly as before.
- Add two expression indexes so the alias probes are equality descents too:
  `ix_additional_usage_alias_limit_latest (lower(limit_name), "window",
  account_id, recorded_at DESC, used_percent DESC, id DESC)` and
  `ix_additional_usage_alias_feature_latest (lower(metered_feature), …)`.
  The canonical probe rides the existing
  `ix_additional_usage_quota_window_latest`.
- Emulate a loose index scan for the unfiltered distinct label listing on
  PostgreSQL (`list_quota_keys` with no `since` bound): iterate distinct
  `(account_id, quota_key, limit_name, metered_feature)` tuples with
  row-value comparison probes over `ix_additional_usage_distinct_labels`
  instead of scanning every row (the request-log facet listing already uses
  this pattern).
- Pass the candidate account ids from the proxy rate-limit-status builder
  (`_build_additional_rate_limits`) so its latest reads probe only the
  accounts it keeps, instead of reading all accounts and filtering in
  Python.
- SQLite paths are unchanged (they already probe per account).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the additional-usage latest-per-account read MUST cost
  O(accounts × match values) index probes, not a scan of the quota key's
  history; the unfiltered distinct label listing MUST NOT scan every
  history row on PostgreSQL.

## Impact

`app/modules/usage/repository.py`,
`app/modules/proxy/_service/rate_limit.py`, one Alembic revision (two
expression indexes; `CREATE INDEX CONCURRENTLY` on PostgreSQL with
invalid-leftover repair, mirroring `20260717_000000`), `app/db/migrate.py`
manual drift index list. No API change; read results are byte-identical.
