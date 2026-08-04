## 1. WebSocket Context Lifecycle

- [x] 1.1 Track successful asynchronous context entry per routed WebSocket attempt.
- [x] 1.2 Exit a WebSocket context during error cleanup only when the client entered it successfully.

## 2. Regression Coverage

- [x] 2.1 Add an in-memory awaitable context regression that fails before entry and proves the original transport error is preserved.
- [x] 2.2 Verify endpoint fallback and successful caller-owned cleanup retain their existing behavior.

## 3. Validation

- [x] 3.1 Run the focused Codex client unit tests, lint, formatting, and type checks.
- [x] 3.2 Run strict OpenSpec validation and verify implementation-to-spec coherence.
