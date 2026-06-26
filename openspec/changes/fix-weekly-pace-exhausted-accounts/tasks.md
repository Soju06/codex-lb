## 1. Spec

- [x] 1.1 Clarify that exhausted but fresh weekly accounts remain in pace totals.

## 2. Implementation

- [x] 2.1 Include `rate_limited` and `quota_exceeded` accounts in backend weekly pace when their weekly usage data is fresh and complete.
- [x] 2.2 Keep reauth-required, paused, deactivated, missing, and stale accounts excluded from weekly pace.
- [x] 2.3 Replace inverted "behind schedule" pace-card wording with over-planned-usage wording.

## 3. Verification

- [x] 3.1 Add integration coverage for a fresh `quota_exceeded` account at 100% weekly usage.
- [x] 3.2 Update weekly credits pace card tests for the clarified wording.
- [x] 3.3 Add integration coverage proving a fresh `reauth_required` account is excluded.
- [x] 3.4 Run targeted dashboard tests, ruff, frontend tests, and OpenSpec validation.
