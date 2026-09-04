## 1. Native failure metadata

- [x] 1.1 Extend native WebSocket transport errors and relay messages with
  bounded phase/detail metadata while keeping public error text unchanged.
- [x] 1.2 Capture queue depth and limit on bounded application-message overflow
  and emit a single structured receive-failure warning.

## 2. Relay persistence

- [x] 2.1 Copy native receive metadata to terminal WebSocket request states after
  replay selection and before request-log finalization.
- [x] 2.2 Cover direct WebSocket and HTTP-bridge terminal paths without changing
  retry, health, or downstream error behavior.

## 3. Tests and validation

- [x] 3.1 Add unit tests for native phase/detail propagation and queue overflow
  diagnostics.
- [x] 3.2 Add relay regression coverage proving request-log metadata is written
  while the public error remains unchanged.
- [x] 3.3 Run focused tests, lint/format checks, and strict OpenSpec validation
  (or the repository's available equivalent when the CLI is unavailable).
