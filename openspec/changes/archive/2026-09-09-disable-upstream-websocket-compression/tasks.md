## 1. Implementation

- [x] 1.1 Pass `compression=None` at the direct-egress `websocket_connect` callsite in
      `app/core/clients/proxy_websocket.py` so upstream direct-egress sockets stop
      offering `permessage-deflate`. Leave the routed aiohttp path, raw-handshake
      transport, and downstream uvicorn `ws_per_message_deflate` untouched.

## 2. Validation

- [x] 2.1 Extend the direct-transport kwargs assertions in
      `tests/unit/test_proxy_websocket_client.py` with `compression is None`.
- [x] 2.2 Run the proxy websocket client unit suite, lint, and strict OpenSpec
      validation.
