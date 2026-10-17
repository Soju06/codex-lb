## 1. Regression Coverage

- [x] 1.1 Add a real direct Responses WebSocket cancellation regression that
  distinguishes the generic task timeout from the scope cleanup budget.
- [x] 1.2 Confirm the regression fails against the baseline implementation and
  passes with the scoped budget.

## 2. Scope Cleanup Budget

- [x] 2.1 Add the fixed normal-operation WebSocket scope cleanup budget.
- [x] 2.2 Preserve the active shared shutdown deadline and one-second generic
  child-task cancellation behavior.
- [x] 2.3 Keep unfinished cleanup tracked and prevent cancellation/lease
  ownership regressions when the bound expires.

## 3. Verification

- [x] 3.1 Run focused WebSocket terminal-cancellation tests.
- [x] 3.2 Run changed-file Ruff check/format, proxy architecture checks, and
  applicable type checks.
- [x] 3.3 Validate the OpenSpec delta and inspect the final diff/status.
- [x] 3.4 Open a Draft PR targeting upstream `main` and add the live 1.23.0
  evidence to issue #1711.
