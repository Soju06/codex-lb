## 1. Implementation

- [x] 1.1 Alembic revision: `ix_additional_usage_alias_limit_latest` and
      `ix_additional_usage_alias_feature_latest` expression indexes
      (PostgreSQL `CREATE INDEX CONCURRENTLY` inside an autocommit block
      with invalid-leftover repair; plain expression indexes elsewhere);
      register both in `_MANUAL_DRIFT_INDEX_REQUIREMENTS`.
- [x] 1.2 Rewrite `AdditionalUsageRepository.latest_by_account` PostgreSQL
      branch as correlated per-account top-1 probes per canonical and alias
      match value, merged with `_merge_latest_additional_usage_entries`.
- [x] 1.3 Loose-scan emulation for `list_quota_keys` on PostgreSQL when no
      `since` bound is given (row-value comparison probes over
      `ix_additional_usage_distinct_labels`).
- [x] 1.4 Pass candidate account ids from
      `ProxyService._build_additional_rate_limits`.

## 2. Validation

- [x] 2.1 Integration coverage: canonical vs alias recency merge parity,
      `since`/`account_ids` bounds, and loose-scan parity with plain
      `DISTINCT` on PostgreSQL.
- [x] 2.2 Existing unit/integration suites pass (`uv run pytest`), `ruff`,
      `ty`, `openspec validate --specs`.
