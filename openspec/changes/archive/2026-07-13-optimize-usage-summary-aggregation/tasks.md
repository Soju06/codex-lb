## 1. Implementation

- [x] 1.1 `aggregate_usage_metrics_since` (totals + top-error + cost-by-model) with helper-exact semantics
- [x] 1.2 `build_usage_metrics_from_aggregate` / `build_usage_cost_from_aggregate`; thread through `metrics_override` / `cost_override`
- [x] 1.3 Remove the post-commit `session.refresh` in `add_log`

## 2. Tests & validation

- [x] 2.1 Equivalence test (SQLite + PostgreSQL) covering reasoning fallback, cached clamps, NULL cost, warmup exclusion
- [x] 2.2 Usage API suite green on both backends; `ruff`/`ty`; `openspec validate --specs`
