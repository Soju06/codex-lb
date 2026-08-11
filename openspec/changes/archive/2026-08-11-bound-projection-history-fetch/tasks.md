## 1. Implementation

- [x] 1.1 `UsageRepository.bulk_history_since`: optional per-account
      `cutoffs`; PostgreSQL recency predicate becomes an OR of per-account
      `(account_id, recorded_at >= cutoff)` ranges. SQLite path unchanged.
- [x] 1.2 `DashboardRepository.bulk_usage_history_since` passes `cutoffs`
      through; `_load_projection_histories` supplies the cutoff maps it
      already computes.

## 2. Validation

- [x] 2.1 Integration coverage: per-account bounding excludes rows older
      than an account's own cutoff while a wider account still gets its
      full window; results match the shared-floor fetch after trimming.
- [x] 2.2 Existing suites pass (`uv run pytest`), `ruff`, `ty`.
