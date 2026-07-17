## 1. Diagnostics

- [x] 1.1 Identify existing WebSocket stale-anchor request state and logging paths.
- [x] 1.2 Add structured stale-anchor diagnostic derivation for anchor source, replay availability, owner lookup, age, and same-session status.
- [x] 1.3 Persist stale-anchor diagnostics in request-log failure metadata without exposing raw response ids or payloads.

## 2. Coverage

- [x] 2.1 Add regression coverage for client-supplied stale-anchor metadata.
- [x] 2.2 Add regression coverage for proxy-injected stale-anchor metadata.
- [x] 2.3 Validate focused tests and OpenSpec artifacts.
