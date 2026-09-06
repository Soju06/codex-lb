## 1. Implementation

- [x] 1.1 Emit the explicit duplicate-tool-call replay failure from direct SSE,
  HTTP bridge, and WebSocket terminal paths.
- [x] 1.2 Preserve non-success settlement and account-health fencing.
- [x] 1.3 Keep the duplicate-tool-call replay terminal out of retry-circuit
  failure classes.

## 2. Validation

- [x] 2.1 Run focused direct-SSE, HTTP bridge, WebSocket, and retry-circuit
  regression tests.
- [x] 2.2 Run strict OpenSpec validation.
