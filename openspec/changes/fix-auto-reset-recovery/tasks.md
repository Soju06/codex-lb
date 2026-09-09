## Implementation
- [x] Add durable single-active reset attempts and migration.
- [x] Persist intent before consumption and confirmed result before usage refresh; reuse the request ID across uncertain retries.
- [x] Refresh standard usage before pool-exhaustion redemption and reconcile previous attempts first.
- [x] Preserve expiry redemption and manual API response compatibility.

## Validation
- [x] Regression tests for restart, unknown outcomes, stale usage, no credits, healthy failover, concurrency, and manual compatibility.
- [x] Migration upgrade/check and downgrade/upgrade on an isolated test database.
- [x] Ruff, imports, focused tests, and OpenSpec validation.
- [ ] Coordinate runtime deployment, preserve rollback, and verify live endpoint plus a Codex worker request.
