## 1. Implementation

- [x] 1.1 Extract only Lite parallel-tool normalization; non-Lite settings and trust rules remain unchanged.
- [x] 1.2 Stock route sent true for Lite (new regression failed); helper/builder, HTTP/compact, bridge and direct trusted-delta tests pass after normalization.

## 2. Validation

- [x] 2.1 Lite selection: 55 passed; overlapping bridge selection: 11 passed. Ruff/format, typing with the project environment, architecture/cancellation/timing, strict change and 65 main specs passed.
- [x] 2.2 Independent beta.9 base; one runtime finalizer change, synthetic tests and this OpenSpec change only.
