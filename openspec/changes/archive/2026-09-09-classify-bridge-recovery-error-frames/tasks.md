# Tasks

## 1. Regression Coverage

- [x] 1.1 Add gate regressions for the terse parameterless ``Invalid `previous_response_id`.`` frame and a frame carrying the classifiable code only in `type`, verifying both misclassify (no recovery) before the fix.
- [x] 1.2 Add anchor-poison regressions: consecutive eventless `stream_incomplete` reader failures with an admission waiter, and consecutive eventless failures through the shared retirement boundary without waiters, verifying neither poisons the anchor before the fix.

## 2. Classifier Routing

- [x] 2.1 Normalize the error code (falling back to `type`) in the bridge-local previous-response recovery gate before all classification checks, matching the WebSocket rewrite path from `classify-invalid-previous-response-id`.

## 3. Anchor Poison Counting

- [x] 3.1 Map both ambiguous eventless retry-circuit classes (`stream_incomplete`, `stream_idle_timeout` and its aliases) to anchor-poison details; keep `clean_close` excluded.
- [x] 3.2 Widen the deferred reader-path poison branch to both classes and thread the poison detail into the poisoned-anchor observability events.
- [x] 3.3 Evaluate the poison threshold at the shared retirement boundary and clear the poisoned durable anchor while the session still owns its durable lease.

## 4. Verification

- [x] 4.1 Run the touched bridge unit and integration suites, ruff, and type checks.
- [x] 4.2 Run strict OpenSpec validation for this change and review the final diff for unrelated changes.
