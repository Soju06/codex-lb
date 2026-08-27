## 1. Regression

- [x] 1.1 Add a deterministic route-level cancellation test that waits for source audio forwarding to begin before cancelling and inspects real reservation persistence.
- [x] 1.2 Confirm the focused test fails on baseline because the exact reservation remains `reserved` and its limit counter remains charged.

## 2. Implementation

- [x] 2.1 Release the owned audio transcription reservation through the established cancellation-deferring cleanup helper and use cancellation-neutral cleanup diagnostics.
- [x] 2.2 Preserve the original cancellation and existing success, forwarding-error, missing-usage, and settlement behavior.

## 3. Verification

- [x] 3.1 Run the focused cancellation regression and existing source-audio success, error, usage, duration, and missing-usage integration tests.
- [x] 3.2 Run Ruff format-check/check and ty on the changed Python files.
- [x] 3.3 Run strict OpenSpec validation for the scoped change and affected `api-keys` spec.
- [x] 3.4 Verify implementation coherence against the OpenSpec change.
- [x] 3.5 Inspect the final diff and worktree status for scope and unrelated changes.
- [x] 3.6 Verify the exceptional cleanup-failure contract with the focused stale-reservation quota-restoration regression.
