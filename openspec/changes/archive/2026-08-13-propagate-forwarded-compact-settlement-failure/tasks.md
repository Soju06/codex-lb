## 1. Settlement Failure Handling

- [x] 1.1 Add a fresh-repository fail-safe release and a trusted `502 usage_settlement_failed` error to `_settle_compact_api_key_usage`.
- [x] 1.2 Propagate trusted usage-settlement failures before compact upstream retry and account-health handling.

## 2. Forwarded Regression

- [x] 2.1 Add a signed owner-forwarded compact integration regression that injects finalization failure and verifies one upstream call, no health-error handling, the 502 error, and a `released` reservation.
- [x] 2.2 Add a unit regression that makes finalization and fail-safe release both fail and verifies the trusted `usage_settlement_failed` error provenance.

## 3. Verification

- [x] 3.1 Prove the regression fails without the behavior fix and passes with it.
- [x] 3.2 Run the focused compact integration slice plus affected ruff, format, type, and proxy-architecture checks.
- [x] 3.3 Validate the scoped OpenSpec change and all main specs strictly, run OpenSpec verification, and inspect the final diff/status.
