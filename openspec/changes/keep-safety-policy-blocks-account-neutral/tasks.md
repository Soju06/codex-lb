## 1. Classification and account health

- [x] 1.1 Match only `misalignment_policy_violation` failures carrying the
  safety-system block message and HTTP status 400 when a status is known.
- [x] 1.2 Return the existing non-retryable classification without recording
  transient, rate-limit, quota, or permanent account-health penalties.
- [x] 1.3 Cover matching and non-matching status, code, and message shapes.

## 2. HTTP bridge behavior

- [x] 2.1 Exclude matching pre-output safety-policy failures from the bridge
  retry-circuit strike path.
- [x] 2.2 Verify the original `response.failed` event, error code, and message
  reach the downstream queue unchanged.

## 3. Verification

- [x] 3.1 Run the focused proxy-utils and HTTP-bridge regressions.
- [x] 3.2 Run changed-file Ruff and formatting checks.
- [x] 3.3 Run strict OpenSpec validation for this change.
