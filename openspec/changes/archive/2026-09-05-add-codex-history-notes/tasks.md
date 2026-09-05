## 1. Contract
- [x] Document endpoints, account scope, opaque transport and failure behavior.
- [x] Sync the verified specification and operator context.

## 2. Implementation
- [x] Add explicit authenticated history and notes routes.
- [x] Preserve upstream payload and protocol headers and redact private diagnostics.
- [x] Reject unscoped and multi-account keys before forwarding.

## 3. Verification
- [x] Test all routes, trailing slashes, auth, scope, unavailable owner, upstream errors and payload privacy.
- [x] Run related routing/client regression tests, lint, type checks and OpenSpec validation.
- [x] Package a reviewable patch and an isolated smoke-test procedure; keep production unchanged.
