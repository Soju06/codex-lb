## 1. Classification and account health

- [x] 1.1 Match only `misalignment_policy_violation` failures carrying the
  safety-system block message and HTTP status 400 when a status is known.
- [x] 1.2 Return the existing non-retryable classification without recording
  transient, rate-limit, quota, or permanent account-health penalties.
- [x] 1.3 Cover matching and non-matching status, code, and message shapes.
- [x] 1.4 Cover the routed Responses stream path, including the preserved
  failure envelope, non-retryable behavior, and unchanged account health.

## 2. Verification

- [x] 2.1 Run the focused streaming route and proxy-utils regressions.
- [x] 2.2 Run changed-file Ruff and formatting checks.
- [x] 2.3 Run strict OpenSpec validation for this change.
