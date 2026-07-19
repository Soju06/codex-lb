## 1. Backend tiebreak

- [x] 1.1 In `_should_prefer_primary_row` (`app/core/usage/__init__.py`), classify each row as real-quota-metadata (positive `window_minutes` AND non-null `reset_at`) versus no-data placeholder, and within the same-fetch margin make a real weekly primary row win over a no-data secondary row (and vice versa).
- [x] 1.2 Gate fetch ordering on BOTH `recorded_at` values being present and differing by strictly more than `SIBLING_FETCH_MARGIN_SECONDS`; within the margin (at most the margin) fall through to the data-aware tiebreak instead of letting a sub-second difference decide. When only one or neither timestamp is present, do NOT let timestamp presence decide — fall through to the data-aware tiebreak so a timestamped placeholder cannot beat an untimestamped real row.
- [x] 1.3 Route the fix through the shared `should_use_weekly_primary` path so account-summary `_effective_usage_windows`, trend `_effective_usage_trend_buckets`, dashboard `normalize_weekly_only_rows`, and dashboard projection `_should_use_weekly_primary_history` all inherit it (consumer-level focused tests are scoped to the core path + dashboard overview; direct trend/projection placeholder regressions are left to a follow-up).

## 2. Regression coverage

- [x] 2.1 Add a unit test proving a same-fetch weekly `primary` row with real quota metadata wins over a no-data `secondary` placeholder (`window_minutes` falsy, `reset_at` null, `used_percent` 0.0, no credit metadata) regardless of which row is milliseconds newer.
- [x] 2.2 Add a unit test proving a genuinely newer real `secondary` row (written beyond the sibling-fetch margin in a later fetch) still supersedes a stale weekly `primary` row, preserving current behavior.
- [x] 2.3 Add a unit test proving two real same-fetch weekly rows are resolved by reset-at precedence and do not flip on sub-second `recorded_at` differences.
- [x] 2.4 Add an integration-level regression proving the dashboard overview weekly remaining percent tracks the real weekly `used_percent` and does not jump to 100% when a no-data secondary placeholder is present (account-summary, trend, and projection consumer paths inherit the shared core fix but are not directly regression-tested here).
- [x] 2.5 Add unit tests for the cross-fetch matrix: stale real primary vs fresh placeholder (newer fetch wins) and stale real primary vs fresh real secondary (newer fetch wins).
- [x] 2.6 Add a unit test pinning the exact `SIBLING_FETCH_MARGIN_SECONDS` boundary (delta == margin is same-fetch; delta > margin is different-fetch).
- [x] 2.7 Add unit tests for the exactly-one-missing-timestamp case via the public path where reachable (untimestamped real primary vs timestamped placeholder -> primary wins; timestamped real primary vs untimestamped placeholder -> primary wins) AND the private `_should_prefer_primary_row` helper for the unreachable-through-public reverse branch (timestamped primary placeholder vs untimestamped real secondary -> secondary wins), since a no-data primary fails the weekly-window guard before the tiebreak.
- [x] 2.8 Add a unit test proving the 5h (`window_minutes == 300`) primary path never enters the weekly tiebreak (`should_use_weekly_primary` returns `False` immediately).
- [x] 2.9 Add helper-level unit tests for: both timestamps None (real beats placeholder both directions); genuine both-placeholder same-fetch (stable default); same-fetch two real rows where the secondary has the later reset_at (secondary wins, locking the reverse reset-precedence direction).

## 3. Validation

- [x] 3.1 Run focused backend tests for `app/core/usage`, account mappers, and dashboard service usage aggregation.
- [x] 3.2 Run repo lint/format (`ruff check` + `ruff format --check`) and type check (`ty`) for touched Python files.
- [x] 3.3 Run strict OpenSpec validation (`openspec validate --specs`) and verify implementation/spec/task coherence.
