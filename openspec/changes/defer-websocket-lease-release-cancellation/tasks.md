## 1. Regression Coverage

- [x] 1.1 Add event-gated connect-attempt and terminal-stream tests that clear ownership before suspending release.
- [x] 1.2 Add event-gated current-account and response-create cleanup tests with repeated cancellation.
- [x] 1.3 Run the focused tests before production edits and record the expected cancellation-order failures.

## 2. Cancellation-Safe Cleanup

- [x] 2.1 Relocate the existing cancellation-deferring task helper to common service support without changing its behavior.
- [x] 2.2 Use the common helper at direct WebSocket connect-attempt and terminal stream-lease release seams.
- [x] 2.3 Use the common helper for current-account and scoped response-create lease cleanup.
- [x] 2.4 Preserve existing normal and release-failure behavior while re-raising cancellation only after release finishes.

## 3. Verification

- [x] 3.1 Run focused connect, terminal, and scope cancellation tests without sleeps or polling.
- [x] 3.2 Run Ruff, ty, LSP diagnostics, and the repository lint target for changed Python files.
- [x] 3.3 Run strict scoped and full OpenSpec validation.
- [x] 3.4 Run an event-gated lifecycle driver proving ownership clear, repeated cancellation, exactly-once release, and release-before-reraise ordering.
- [x] 3.5 Review the final diff, task completion, and cleanup evidence.
