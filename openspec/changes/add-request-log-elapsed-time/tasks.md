## 1. Spec

- [x] 1.1 Add OpenSpec requirements for persisted upstream elapsed timing and Request Details rendering
- [x] 1.2 Run `openspec validate --specs`

## 2. Tests

- [x] 2.1 Add backend coverage for nullable `elapsed_ms` persistence and request-log API exposure
- [x] 2.2 Add frontend coverage for elapsed-time formatting and separate upstream/total elapsed rendering

## 3. Implementation

- [x] 3.1 Add the nullable `elapsed_ms` request-log column and wire it through ORM and request-log schemas
- [x] 3.2 Capture `elapsed_ms` at the unary, streaming, WebSocket, and warmup request-log write paths without changing `latency_ms` semantics
- [x] 3.3 Show `Upstream elapsed` and `Total elapsed` in the Request Details dialog using the shared duration formatter

## 4. Verification

- [x] 4.1 Run targeted backend and frontend test commands for the changed request-log paths
- [x] 4.2 Run repo validation relevant to the touched files and confirm the OpenSpec change is implementation-ready
