## 1. Implementation

- [x] 1.1 Guard the generic HTTP bridge session-creation arm so a previous-response reuse selection cannot publish an inflight future.
- [x] 1.2 Keep the existing janitor and all non-reuse create-chain arms unchanged.

## 2. Verification

- [x] 2.1 Run the F1 bughunt regression and confirm it fails on the baseline and passes after the fix, including the second-request reuse assertion.
- [x] 2.2 Run the HTTP bridge unit and integration suites, including the existing live-inflight janitor test.
- [x] 2.3 Validate OpenSpec artifacts and inspect the final diff/status before committing.
