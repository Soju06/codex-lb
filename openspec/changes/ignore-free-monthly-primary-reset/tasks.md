## 1. Spec And Regression Coverage

- [x] 1.1 Add an OpenSpec delta for zero-capacity monthly primary windows.
- [x] 1.2 Add an `/api/accounts` regression that reproduces a free account stuck in `rate_limited` from a monthly primary snapshot.
- [x] 1.3 Add a load-balancer state regression that proves non-5h zero-capacity primary windows are ignored.

## 2. Implementation

- [x] 2.1 Add a shared helper that identifies the canonical primary-window duration.
- [x] 2.2 Use that helper when deciding whether zero-capacity primary rows may still drive rate-limit recovery.
- [x] 2.3 Keep stale primary-row recovery aligned with the free-account monthly-only quota model.

## 3. Verification

- [x] 3.1 Run the focused pytest coverage for the touched account/load-balancer paths.
- [x] 3.2 Run `openspec validate` for the new change.
