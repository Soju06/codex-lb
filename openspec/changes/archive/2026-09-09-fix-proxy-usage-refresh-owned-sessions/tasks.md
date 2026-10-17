## 1. Session Lifetime

- [x] 1.1 Detach pre-refresh ORM rows before closing the request-adjacent repository scope.
- [x] 1.2 Run owned usage refresh reads and writes through short-lived background repositories without retaining a session across upstream I/O.
- [x] 1.3 Preserve caller-independent singleflight ownership when the public usage request is cancelled.

## 2. Regression Coverage

- [x] 2.1 Add payload-scope detachment coverage.
- [x] 2.2 Add public `/api/codex/usage` cancellation coverage that verifies the initial scope closes and no payload-read scope reopens.
- [x] 2.3 Preserve owned refresh completion after caller cancellation.

## 3. Verification

- [x] 3.1 Run focused usage updater, rate-limit, and Codex usage integration tests.
- [x] 3.2 Run changed-file Ruff and type checks.
- [x] 3.3 Validate the OpenSpec delta strictly.
