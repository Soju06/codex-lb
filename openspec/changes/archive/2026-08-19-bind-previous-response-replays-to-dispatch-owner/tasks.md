## 1. Regression coverage

- [x] 1.1 Add a deterministic HTTP streaming regression proving account-bound
      encrypted reasoning never dispatches to a Trusted Access replacement.
- [x] 1.2 Add direct WebSocket regressions for unanchored and verified-fresh
      account-bound request bodies.
- [x] 1.3 Add HTTP bridge coverage for owner exclusion and account-neutral
      replacement controls.
- [x] 1.4 Add a confirmed pre-dispatch regression proving owner registration
      waits for actual upstream dispatch.

## 2. Owner-fencing implementation

- [x] 2.1 Route every replay candidate through the canonical account-neutral
      fresh-replay predicate.
- [x] 2.2 Bind nonportable HTTP stream payloads to their first dispatch owner
      and require that owner during later selections.
- [x] 2.3 Enforce the same binding in HTTP bridge and direct WebSocket account
      switching without changing settlement ordering.

## 3. Verification and publication

- [x] 3.1 Capture genuine focused RED, implement the minimal owner fence, and
      run focused HTTP/bridge/WebSocket tests GREEN.
- [x] 3.2 Run diagnostics, Ruff, typecheck, architecture gates, full affected
      tests, and strict affected OpenSpec validation.
- [x] 3.3 Execute an isolated real-surface account-switch scenario proving no
      cross-account dispatch and an account-neutral control.
- [x] 3.4 Complete independent review and sync the verified change for archive.
