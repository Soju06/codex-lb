## Why

Responses `image_generation` calls can return large inline base64 image payloads. The current auto upstream transport logic still prefers websocket for websocket-preferred models such as `gpt-5.4`, which makes those legitimate image outputs hit the proxy's websocket frame ceiling and fail locally with `1009 message too big`.

## What Changes

- Modify auto upstream transport selection so Responses requests containing the built-in `image_generation` tool use upstream HTTP/SSE instead of upstream websocket.
- Keep explicit operator overrides intact: `upstream_stream_transport=http` and `upstream_stream_transport=websocket` continue to win over auto policy.
- Add regression coverage for transport resolution and end-to-end streaming path selection when `image_generation` is present.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: auto upstream transport selection changes for Responses requests that include the `image_generation` built-in tool.

## Impact

- Affects upstream transport selection in [app/core/clients/proxy.py](/Users/hughdo/Desktop/Proj/codex-lb/app/core/clients/proxy.py).
- Adds regression coverage in `tests/unit/test_proxy_utils.py`.
- Keeps compact request sanitization and explicit transport overrides unchanged.
