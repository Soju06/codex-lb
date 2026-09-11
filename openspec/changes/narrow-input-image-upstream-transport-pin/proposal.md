# Change: narrow-input-image-upstream-transport-pin

## Why

Any Responses request whose `input` carries an `input_image` part was forced
onto the upstream HTTP transport, whatever the operator configured. Codex keeps
earlier screenshots in the thread history, so a single pasted image dragged
every later turn of that conversation onto upstream HTTP for the rest of the
thread — losing upstream WebSocket connection reuse and prompt-cache affinity
for that traffic (#2363).

The pin was never required for correctness. It arrived in `bcd63c827` as the
transport half of a bridge bypass whose stated purpose was freeing HTTP bridge
pending slots (#903), and native downstream WebSocket clients already send
inline `data:` images over the upstream WebSocket today with no bypass at all.
Only two WebSocket-specific hazards actually need upstream HTTP: a payload over
the WebSocket frame budget, where the oversize slimmer strips every historical
inline image, and an external `http(s)` image URL, which the image inliner
leaves in place when its fetch fails.

## What Changes

- One shared predicate, `_input_image_request_requires_http_upstream`, decides
  whether an `input_image` request keeps the upstream HTTP pin: only when the
  serialized payload exceeds the WebSocket frame budget
  (`_ws_transport_payload_budget_bytes()`), or when the payload still carries an
  external `http(s)` image URL.
- Both pin sites call it. `_stream_http_bridge_or_retry` no longer computes
  `force_upstream_stream_transport = "http" if image_request else None`, and
  `_stream_with_retry` no longer folds `input_image` into the
  `has_image_generation_tool` argument of `_resolve_stream_transport`.
- The HTTP bridge bypass is unchanged: an image request still bypasses the
  bridge, still records `http_bridge_routing{stage="bypass",reason="image"}`,
  and still logs the same line. The `image_generation` pin is unchanged.
- No new setting, metric, or metric label.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the image bridge bypass no longer pins the upstream
  stream transport, and the downstream-HTTP transport precedence narrows its
  `input_image` clause to oversized payloads and external image URLs.

## Impact

- Image threads rejoin the sticky WebSocket/promotion population, which changes
  prompt-cache affinity and account stickiness for that traffic.
- With the pin cleared, an image request during an upstream WebSocket outage now
  falls into the existing `recent_ws_failure` branch and is degraded to HTTP
  there, with the bypass counter and log that branch already emits.
- `codex_lb_upstream_transport_decisions_total` will show
  `upstream_transport="http",policy="explicit"` falling and `"auto"` rising for
  image traffic; `http_bridge_routing{reason="image"}` stays flat.
