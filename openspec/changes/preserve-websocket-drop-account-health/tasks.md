## 1. Upstream WebSocket account health

- [x] 1.1 Penalize an account when an unclassified upstream WebSocket receive
  close settles pending requests as `stream_incomplete`.
- [x] 1.2 Preserve the account-neutral exception only for classified
  `proxy_network_unavailable` failures.
- [x] 1.3 Cover a classified `stream_incomplete` relay failure through
  `_relay_upstream_websocket_messages` and assert the health handler runs.

## 2. Validation

- [x] 2.1 Run focused WebSocket relay tests, lint/format, and strict OpenSpec
  validation.
