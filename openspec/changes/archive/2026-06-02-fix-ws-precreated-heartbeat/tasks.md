## 1. Specification
- [x] Add a responses-api-compat delta for Codex-native pre-created WebSocket heartbeats and public `/v1` isolation.

## 2. Tests
- [x] Add/update unit coverage proving Codex-native WebSocket relays emit a parseable vendor heartbeat before `response.created`.
- [x] Add unit coverage proving OpenAI-style `/v1` WebSocket relays do not emit the vendor heartbeat before `response.created`.
- [x] Keep existing post-created `response.in_progress` heartbeat coverage green.

## 3. Implementation
- [x] Teach the WebSocket relay whether the downstream route is Codex-native.
- [x] Emit `codex.keepalive` for pending unresolved Codex-native requests when upstream is silent.
- [x] Preserve the existing `response.in_progress` keepalive after a response id is known.

## 4. Verification
- [x] Run focused websocket regression tests.
- [x] Run OpenSpec validation.
- [x] Run the relevant Python test subset / lint gates.
