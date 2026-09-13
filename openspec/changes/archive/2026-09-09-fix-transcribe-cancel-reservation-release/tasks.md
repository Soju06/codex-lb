## 1. Regression

- [x] 1.1 Add a deterministic subscription-backed transcription route cancellation test that waits for upstream forwarding to begin, uses a release spy with an await checkpoint, and verifies cancellation propagation plus exactly-once persisted release and quota restoration.
- [x] 1.2 Confirm the focused regression fails on baseline because the release is interrupted and the reservation remains `reserved`.

## 2. Implementation

- [x] 2.1 Release the owned transcription reservation through the established cancellation-deferring cleanup helper, logging cleanup failures without replacing the original cancellation.
- [x] 2.2 Preserve exactly-once cleanup and existing success, `ProxyResponseError`, billing, and response behavior.

## 3. Verification

- [x] 3.1 Run the focused transcription and source-audio integration test files.
- [x] 3.2 Run Ruff check on the changed Python files.
- [x] 3.3 Run strict OpenSpec validation for the scoped change and affected `api-keys` spec.
- [x] 3.4 Inspect the final diff and worktree status for scope and unrelated changes.
