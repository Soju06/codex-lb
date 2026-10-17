## 1. Shared-Future Wait Conversion

- [x] 1.1 Audit remaining `asyncio.shield` calls in `app/` and confirm only usage-refresh singleflight matches the shared, many-waiter class.
- [x] 1.2 Replace both `_UsageRefreshSingleflight.run` shared-task waits with `wait_on_shared_future` while preserving cancellation and exception semantics.

## 2. Regression Coverage

- [x] 2.1 Test that concurrent usage-refresh waiters receive the same result and cancelling all but one does not cancel the factory task.
- [x] 2.2 Test that cancelled usage-refresh waiters detach with one bounded fan-out callback on the in-flight task.
- [x] 2.3 Test that `join_existing=False` waits for the predecessor before starting a successor through the shared-future helper.
- [x] 2.4 Temporarily restore the shield implementation, run the callback fan-out regression test, and record the failing sabotage result.

## 3. Verification

- [x] 3.1 Run the targeted usage updater and shared-future waiter unit tests.
- [x] 3.2 Run the repository lint source of truth and strict OpenSpec validation.
- [x] 3.3 Record commands and exit codes in `/tmp/codex-lb-1896-verification.md`.
