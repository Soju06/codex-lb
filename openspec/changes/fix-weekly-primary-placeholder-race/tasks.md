## 1. Backend tiebreak

- [x] 1.1 In `_should_prefer_primary_row` (`app/core/usage/__init__.py`), classify each row as real-quota-metadata (positive `window_minutes` AND non-null `reset_at`) versus no-data placeholder, and make a real weekly primary row win over a no-data secondary row (and vice versa) before any `recorded_at` comparison.
- [x] 1.2 Treat rows whose `recorded_at` differs by less than `_SIBLING_FETCH_MARGIN_SECONDS` as same-fetch and fall through to reset-at precedence / the stable default instead of letting the sub-second difference decide.
- [x] 1.3 Ensure the change is exercised by all four shared-path consumers: account-summary `_effective_usage_windows`, trend `_effective_usage_trend_buckets`, dashboard `normalize_weekly_only_rows`, and dashboard projection `_should_use_weekly_primary_history`.

## 2. Regression coverage

- [x] 2.1 Add a unit test proving a weekly `primary` row with real quota metadata wins over a no-data `secondary` placeholder (`window_minutes` falsy, `reset_at` null, `used_percent` 0.0, no credit metadata) regardless of which row is microseconds newer.
- [x] 2.2 Add a unit test proving a genuinely newer real `secondary` row (written beyond the sibling-fetch margin in a later fetch) still supersedes a stale weekly `primary` row, preserving current behavior.
- [x] 2.3 Add a unit test proving two real same-fetch weekly rows are resolved by reset-at precedence and do not flip on sub-second `recorded_at` differences.
- [x] 2.4 Add an integration-level regression proving the dashboard/account-summary weekly remaining percent tracks the real weekly `used_percent` and does not jump to 100% when a no-data secondary placeholder is present.

## 3. Validation

- [x] 3.1 Run focused backend tests for `app/core/usage`, account mappers, and dashboard service usage aggregation.
- [x] 3.2 Run repo lint/format (`ruff`) for touched Python files.
- [x] 3.3 Run strict OpenSpec validation (`openspec validate --specs`) and verify implementation/spec/task coherence.
