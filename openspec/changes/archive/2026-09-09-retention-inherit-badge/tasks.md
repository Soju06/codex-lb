# Tasks

## 1. Dashboard

- [x] 1.1 `DataRetentionSettings` renders `InheritBadge` (`request_log_retention_days` → `requestLogRetentionOverrideDays`, `usage_history_retention_days` → `usageHistoryRetentionOverrideDays`) under each window; bespoke hint removed; tri-state input, floors and validation messages unchanged.
- [x] 1.2 `settings.retention.inheritedHint` removed from `en`, `ko`, `zh-CN` (key-parity test).
- [x] 1.3 Tests: `Default (0)` badges with empty inputs, reset clears only the dashboard-owned window with an explicit `null`, no badge or hint against a backend without `provenance`.
- [x] 1.4 Before/after screenshots (light and dark) in the PR.

## 2. Spec

- [x] 2.1 `configuration-tiers`: MODIFIED requirement removes the retention allowance and adds the "Retention windows use the shared inherited affordance" scenario.

## 3. Verification

- [x] 3.1 Frontend lint/typecheck/vitest, `make lint`, `uv run ty check`, `openspec validate --specs --strict`, `openspec validate retention-inherit-badge --strict`, `python3 .github/scripts/check_simplicity_budgets.py`.
