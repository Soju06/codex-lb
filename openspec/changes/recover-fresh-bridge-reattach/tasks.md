## 1. Fresh Reattach Recovery

- [x] 1.1 Detect verified full resends whose durable anchor would be the first request on a fresh bridge.
- [x] 1.2 Project proxy-injected anchors into a fresh account-neutral request before dispatch.
- [x] 1.3 Retry an eventless verified client anchor once without the anchor and never repeat that replay.

## 2. Cancellation Ownership

- [x] 2.1 Make the startup first-item handoff explicitly closeable and idempotent.
- [x] 2.2 Close the handoff from the ASGI response lifecycle even before the first body poll.

## 3. Verification

- [x] 3.1 Cover proxy-injected and client-supplied verified full-resend recovery.
- [x] 3.2 Cover unsafe replay rejection, bounded eventless failure, and downstream teardown.
- [x] 3.3 Run focused tests, bridge integration, lint, type checking, architecture, and OpenSpec validation.
