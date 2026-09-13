# Design

## Error classification

The WebSocket handshake layer exposes a pure classifier
(`_is_upstream_edge_challenge`) that requires all of the following:

1. HTTP status `403`.
2. A challenge marker (`cf-mitigated: challenge`) or an equivalent Cloudflare
   server marker together with an HTML response body containing challenge
   evidence.

JSON/OpenAI error envelopes and unmarked proxy HTML are not challenge
responses. The classifier returns false for 401, 404, 426, 429, 5xx, and TLS
errors; the fallback decision additionally returns false when the transport
mode is forced.

Body evidence is transport-dependent: the raw-handshake opener surfaces the
response body in the handshake error, while aiohttp's own `ws_connect` raises
a fixed diagnostic, leaving the `cf-mitigated` header as the only effective
evidence on that path. Both stay fail-closed on a miss.

## Recovery

A classified direct-connect edge challenge is stamped with the existing
websocket transport-failure provenance
(`upstream_websocket_transport_unavailable`), so it reuses the established
recovery lattice unchanged:

- the connect failover decision surfaces the failure without recording an
  account penalty and arms the bounded transport-failure marker;
- responses WebSocket routes deny the next handshake with HTTP 426, which is
  the only signal that activates Codex clients' HTTP transport fallback;
- HTTP responses paths bypass the bridge and pin the upstream transport to
  HTTP while the marker is armed, and a pre-submit bridge session-creation
  challenge replays once over raw HTTP under the existing replay-safety
  proof (no upstream output, no unsettled API-key reservation, no prepared
  anchor).

For `auto` transport on the raw streaming path, a classified challenge on the
websocket handshake triggers the existing single in-request HTTP retry that
today only fires on HTTP 426. Routed handshakes preserve the classification
as a flag on `CodexTransportError` (headers evidence only — the routed
handshake error does not surface the response body) and use the same
in-request retry; they do not arm the instance-wide marker because routed
egress is route/account-scoped evidence.

Forced WebSocket mode preserves the visible error and never retries over
HTTP.

## Observability and isolation

Request logs retain the upstream status and normalized error code without
storing challenge HTML. Client-facing errors are OpenAI-shaped and credential
safe.
