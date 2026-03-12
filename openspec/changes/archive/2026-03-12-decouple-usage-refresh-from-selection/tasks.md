## 1. Spec
- [x] 1.1 Add query-caching requirement that request-path account selection uses cached usage rows and never refreshes usage inline.

## 2. Tests
- [x] 2.1 Add a regression test proving `LoadBalancer.select_account()` does not call `UsageUpdater.refresh_accounts()`.

## 3. Implementation
- [x] 3.1 Remove inline usage refresh from `LoadBalancer.select_account()` while keeping selection semantics based on cached rows.

## 4. Verification
- [x] 4.1 Run targeted unit/integration tests for proxy load balancing.
- [x] 4.2 Run `openspec validate --specs`.
