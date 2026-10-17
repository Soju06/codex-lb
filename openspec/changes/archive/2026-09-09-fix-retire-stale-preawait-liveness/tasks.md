## 1. Regression coverage

- [x] 1.1 Add a deterministic unit regression that injects a response event during retry-circuit suspension and asserts no retirement.
- [x] 1.2 Add a control assertion that an eventless stale session is still retired.
- [x] 1.3 Run the new regression on origin/main and record the expected failure before implementation.

## 2. Implementation

- [x] 2.1 Re-sample response-event and response-created liveness under the bridge/pending locks immediately before the close decision.
- [x] 2.2 Preserve registry cleanup, close idempotence, retry-circuit accounting, and stale-retire logging.

## 3. Verification

- [x] 3.1 Run the targeted unit regression and control.
- [x] 3.2 Run the HTTP bridge unit and integration suites, recording the known reconnect baseline failures.
- [x] 3.3 Validate OpenSpec and commit the focused change without pushing.
