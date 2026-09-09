## 1. Regression Coverage

- [x] 1.1 Add deterministic route-level coverage for limited-key image generation and edit requests cancelled while the first upstream SSE read is blocked.
- [x] 1.2 Confirm the regression fails on baseline because cancellation propagates but the route-owned reservation is not released exactly once.
- [x] 1.3 Add focused priming coverage for cleanup ordering, repeated cancellation, explicit iterator close, and cleanup-failure terminal preservation.

## 2. First-Frame Cancellation Cleanup

- [x] 2.1 Close the upstream iterator and invoke the Images-owned error callback through cancellation-deferring cleanup when `CancelledError` interrupts first-frame priming.
- [x] 2.2 Preserve the original terminal when cleanup fails, retain existing `ProxyResponseError` behavior, and leave post-first-frame image billing and captured-token finalization unchanged.
- [x] 2.3 Reproduce synchronous coroutine-close `GeneratorExit`, document why it cannot await cleanup, and keep request-owned asynchronous cleanup limited to `CancelledError`.

## 3. Verification

- [x] 3.1 Run the new generation/edit cancellation regression and the complete Images integration test file.
- [x] 3.2 Run Ruff check on every changed Python file.
- [x] 3.3 Run scoped and repository-spec OpenSpec validation and verify the change artifacts are coherent.
- [x] 3.4 Inspect the final diff and worktree status for scope, simplicity, and unrelated changes.
- [x] 3.5 Distinguish iterator-close and reservation-release failure outcomes so stale reclamation remains conditional on release failure.
