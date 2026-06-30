## 1. Codex Review Evidence

- [x] 1.1 Filter unresolved Codex inline review threads by current-head evidence.
- [x] 1.2 Preserve body-head fallback only when the review comment body mentions the current head.
- [x] 1.3 Treat threads with neither current-head commit metadata nor current-head body evidence as stale.

## 2. Check Run Evidence

- [x] 2.1 Deduplicate same-named check runs by start/creation recency.
- [x] 2.2 Keep completion time as a fallback when start/creation metadata is absent.
- [x] 2.3 Cover late-finishing superseded runs with regression tests.

## 3. Verification

- [x] 3.1 Run focused unit tests for the synchronizer.
- [x] 3.2 Run ruff format/check and ty on the synchronizer and tests.
