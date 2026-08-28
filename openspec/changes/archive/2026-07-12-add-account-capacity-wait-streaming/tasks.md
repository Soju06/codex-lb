## 1. Streaming account-capacity waits

- [x] 1.1 Detect recoverable account-selection retry hints without treating permanent no-account states as waitable.
- [x] 1.2 Keep HTTP/SSE, HTTP bridge, and WebSocket streams alive while waiting for account capacity to recover.
- [x] 1.3 Bound capacity waits by the original request budget so a single request cannot wait indefinitely.
- [x] 1.4 Add regression coverage for wait payloads, HTTP bridge keepalives, WebSocket keepalives, and budget exhaustion.

## 2. Validation

- [x] 2.1 Run targeted proxy unit and integration tests.
- [x] 2.2 Validate the OpenSpec change with `openspec validate add-account-capacity-wait-streaming --strict`.
