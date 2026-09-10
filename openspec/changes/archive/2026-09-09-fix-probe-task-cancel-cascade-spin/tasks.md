## 1. Break the Cancellation Cascade

- [x] 1.1 Await the startup-probe task in `_prepend_first_task` through `wait_on_shared_future`.
- [x] 1.2 Await the cancelled pending chunk task in `inject_sse_keepalives` teardown, and the cancelled probe task in `_prepend_first_task` teardown, through the canonical cancellation-deferring helper so they settle before the iterator chain is closed.
- [x] 1.3 Document the tracked-task limitation of the anyio shield in the canonical defer-cancellation helper.

## 2. Regression Coverage

- [x] 2.1 Add a level-cancelled Starlette-shaped consumer regression asserting the probe task is cancelled at most once while its cleanup is blocked, and settles after release.
- [x] 2.2 Add the same regression for the keepalive chunk task, including that teardown waits for it and closes the source only after it settles.

## 3. Validation

- [x] 3.1 Run focused cancellation tests, lint, format, type, and architecture checks.
- [x] 3.2 Run strict change-local OpenSpec validation.
