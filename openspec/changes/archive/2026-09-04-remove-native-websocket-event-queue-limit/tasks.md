## 1. Native WebSocket queue

- [x] 1.1 Create native WebSocket transport-event queues without a fixed event-count capacity.
- [x] 1.2 Preserve the separate bounded application-message queue and existing overflow cleanup.

## 2. Regression coverage and verification

- [x] 2.1 Add a native WebSocket burst regression exceeding the former 64-event limit and asserting ordered delivery.
- [x] 2.2 Run focused native-egress tests, lint, and strict OpenSpec validation.
