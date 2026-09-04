## 1. Account Health Classification

- [x] 1.1 Classify `usage_limit_reached` as quota exhaustion while preserving ordinary rate-limit classification.
- [x] 1.2 Verify the shared stream health handler marks usage-exhausted accounts quota-exceeded.
- [x] 1.3 Preserve explicit quota state after its debounce while refreshed long-window usage remains exhausted.

## 2. Regression Coverage

- [x] 2.1 Update classifier and WebSocket failover tests for quota-health handling.
- [x] 2.2 Add state-builder and database-backed selection regressions for fresh-but-exhausted long-window usage.
- [x] 2.3 Run focused proxy tests, lint, type checks, and strict OpenSpec validation.
