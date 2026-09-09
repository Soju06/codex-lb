## 1. Regression

- [x] 1.1 Add a deterministic route-level cancellation test that waits for source embeddings forwarding to begin before cancelling.
- [x] 1.2 Confirm the focused test fails on baseline because the exact created reservation remains `reserved`.

## 2. Implementation

- [x] 2.1 Release the owned embeddings reservation through the established cancellation-deferring cleanup helper.
- [x] 2.2 Preserve the original cancellation and existing success, forwarding-error, missing-usage, and settlement behavior.

## 3. Verification

- [x] 3.1 Run the focused cancellation regression and existing embeddings success/error/usage integration tests.
- [x] 3.2 Run Ruff format-check/check and ty on changed Python files.
- [x] 3.3 Run strict OpenSpec validation and build the package outside the worktree.
- [x] 3.4 Run a temporary helper-level cancellation driver that prints exactly `RELEASE_CALLS=1`, then remove all QA artifacts.
- [x] 3.5 Inspect the final diff and worktree status for scope and unrelated changes.
