## 1. Continuity Contract

- [x] 1.1 Update bridge-local continuity-loss paths to return retryable errors instead of raw `previous_response_not_found`.
- [x] 1.2 Fail closed on hard-continuity owner/ring lookup errors instead of degrading into unpinned or local recovery.

## 2. Regression Coverage

- [x] 2.1 Add bridge regression tests for missing turn-state alias and inflight-follower continuity loss.
- [x] 2.2 Add lookup-failure regression tests for websocket or HTTP fallback `previous_response_id` flows and hard bridge owner lookup failures.

## 3. Verification

- [x] 3.1 Run targeted continuity test suites covering bridge, websocket, and HTTP fallback paths.
- [x] 3.2 Run full pytest and confirm no broader regressions.
