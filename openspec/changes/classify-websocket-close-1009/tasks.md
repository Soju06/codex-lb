## 1. Implementation

- [x] 1.1 Extract only close-1009 classification and terminal normalization; no settings, image, cap or IPC diff.
- [x] 1.2 Stock route test failed because the size rejection was replayed instead of delivered. HTTP/SSE tests verify original error, one dispatch, no exclusion/penalty, reservation cleanup and same-account recovery.
- [x] 1.3 Direct WebSocket route tests and non-1009 controls pass. Combined new acceptance: 22 passed.

## 2. Validation

- [x] 2.1 New acceptance 22 passed; existing close/replay selection 45 passed. Ruff/format, touched-code/test typing, architecture/cancellation/timing, strict change and 65 main specs pass.
- [x] 2.2 Independent beta.9 worktree; only seven runtime files plus synthetic tests and this change. Packaging and remote actions are handled separately by the operator.
