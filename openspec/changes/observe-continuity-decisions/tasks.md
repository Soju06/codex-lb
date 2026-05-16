## 1. Continuity Decision Signals

- [x] 1.1 Add structured continuity decision logging for owner resolution and fail-closed/rewrite paths.
- [x] 1.2 Add Prometheus counters for continuity owner-resolution sources and fail-closed reasons.

## 2. Regression Coverage

- [x] 2.1 Add unit tests for continuity decision logs and metrics on representative websocket and HTTP bridge paths.

## 3. Verification

- [x] 3.1 Run targeted observability and continuity test suites.
- [x] 3.2 Run `ruff`, `openspec validate --specs`, and full `pytest`.
