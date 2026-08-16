## 1. Regression Coverage

- [x] 1.1 Add a parameterized `ReportsService` regression for both Casablanca
  offset transitions plus UTC, stable-offset Casablanca, and invalid-zone
  fallback controls using fixed two-day totals.
- [x] 1.2 Confirm the affected transition cases fail on the original
  UTC-derived divisor before applying the fix.

## 2. Service Fix

- [x] 2.1 Make both per-day averages use the existing inclusive local
  `window_days` value without changing repository inputs or other report
  fields.

## 3. Verification

- [x] 3.1 Run the service regression and focused existing Reports API tests for
  timezone boundaries, fallback, filters, totals, daily rows, comparison, and
  range limits.
- [x] 3.2 Run affected Ruff, type, OpenSpec, diff, and worktree-status checks.
- [x] 3.3 Capture privacy-safe deterministic before/after dashboard evidence
  for an affected Casablanca range and verify the resulting media.
