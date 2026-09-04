## 1. Native HTTP queue

- [x] 1.1 Create native HTTP response event queues without a fixed event-count capacity.
- [x] 1.2 Preserve bounded native WebSocket queue construction and overflow handling.

## 2. Regression coverage and verification

- [x] 2.1 Replace the HTTP overflow regression with a burst-delivery regression exceeding the former 64-event limit.
- [x] 2.2 Run focused native-egress tests, lint, and strict OpenSpec validation.
