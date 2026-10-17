## 1. Reproduce

- [x] 1.1 Add a regression through the dashboard Force Probe endpoint using real account and usage repositories and rollback-on-close sessions.
- [x] 1.2 Confirm the regression fails on the unchanged implementation with expired ORM access during advisory settlement.

## 2. Implement

- [x] 2.1 Snapshot the account and relevant usage rows while their repository session is open.
- [x] 2.2 Preserve the runtime health-version guard, usage normalization, failed-probe handling, and absent-account behavior.
- [x] 2.3 Cover populated, absent, and partial usage plus newer concurrent health evidence with real session teardown.

## 3. Verify

- [x] 3.1 Run focused Force Probe and load-balancer concurrency tests, lint, type checks, and architecture guards.
- [x] 3.2 Update the owning specification and context and validate strictly.
- [x] 3.3 Complete independent review and archive the verified change.
