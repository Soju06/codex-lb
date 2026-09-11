## 1. Extend the Gate

- [x] 1.1 Classify deferring task creations (iterator step, iterator-driving or helper-calling module function, probe factory) and `asyncio.Task` parameters.
- [x] 1.2 Reject bare awaits of those names; allow proxy/helper waits and awaits settled through `asyncio.wait`.
- [x] 1.3 Report a distinct reason per violation kind.

## 2. Repository Conformance

- [x] 2.1 Convert `_iter_sse_events._cancel_pending_chunk` to the canonical deferring wait.
- [x] 2.2 Confirm the gate passes on `main` and rejects the pre-fix SSE injector and startup-probe code.

## 3. Regression Coverage and Validation

- [x] 3.1 Add unsafe/safe fixtures and a CLI-output test for the new rule.
- [x] 3.2 Run focused tests, lint, format, type, and architecture checks; run strict change-local OpenSpec validation.
