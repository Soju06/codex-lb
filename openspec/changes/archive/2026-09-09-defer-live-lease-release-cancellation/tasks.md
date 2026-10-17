## 1. Regression Coverage

- [x] 1.1 Add an event-gated Live cancellation test that suspends lease release
- [x] 1.2 Capture the current release interruption as RED

## 2. Cancellation-Safe Release

- [x] 2.1 Reuse the established cancellation-deferral helper for Live release
- [x] 2.2 Preserve exactly-once release, close behavior, and reraised cancellation

## 3. Verification

- [x] 3.1 Run focused Live tests and static checks
- [x] 3.2 Validate scoped OpenSpec and the affected main capabilities
- [x] 3.3 Execute an event-order driver and verify cleanup
