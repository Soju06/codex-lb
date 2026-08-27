# Design

## Error classification

The WebSocket handshake layer will expose a pure classifier that requires all
of the following:

1. HTTP status `403`.
2. A challenge marker (`cf-mitigated: challenge`) or an equivalent Cloudflare
   server marker together with an HTML response body containing challenge
   evidence.

JSON/OpenAI error envelopes and unmarked proxy HTML are not challenge
responses. The classifier returns false for 401, 404, 426, 429, 5xx, TLS
errors, and forced transport mode.

## Recovery

For `auto` transport, a classified challenge is pre-dispatch transport
evidence. The existing Responses stream path may retry once over HTTP using the
same account and request payload. The retry is allowed only before any
upstream event is observed and only when no API-key reservation or hard
continuity/file ownership is unsettled. Forced WebSocket mode preserves the
visible error.

The failure must not increment account error backoff. Connection leases and
API-key reservations are released exactly once on both success and failure.

## Observability and isolation

Request logs retain the upstream status and normalized error code without
storing challenge HTML. Client-facing errors are OpenAI-shaped and credential
safe. Docker validation runs against a separate image/container/network/volume
and uses per-worker temporary `HOME` directories.
