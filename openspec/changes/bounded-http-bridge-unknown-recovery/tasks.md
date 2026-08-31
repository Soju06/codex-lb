## 1. Implementation

- [x] 1.1 Add bounded recent-UNKNOWN repository lookup scoped by session,
  model, creation age, and result limit.
- [x] 1.2 Add the supporting operation index and Alembic revision.
- [x] 1.3 Gate recent-parent recovery behind parked recovery and require one
  exact canonical request-body match with a nonblank parent.
- [x] 1.4 Rebind the matched durable operation before submission and preserve
  the existing atomic recovery claim/fail-closed behavior.
- [x] 1.5 Emit bounded lookup outcome diagnostics (candidate absence, body
  mismatch, and ambiguity) without logging request payloads or widening the
  recovery fence.
- [x] 1.6 Expose the repository's UNKNOWN-operation lookups through the
  durable session coordinator used by the production service.

## 2. Validation

- [x] 2.1 Add repository, enabled-path, and disabled-path regressions.
- [x] 2.2 Run affected unit suites, Ruff, compile checks, migration graph
  checks, and diff checks.
