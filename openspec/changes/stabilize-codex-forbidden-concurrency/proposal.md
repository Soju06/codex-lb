# Recover Codex edge-challenge WebSocket failures

## Why

Codex clients can receive a terminal `403 Forbidden` when an upstream edge
rejects the Responses WebSocket handshake with a Cloudflare or equivalent
browser challenge. This is distinct from codex-lb's local firewall and API-key
permission errors. The transport classifier treats every 403 as a
non-retryable permission failure, so an otherwise usable HTTP Responses path
cannot recover, and the challenge poisons the owning account's health even
though it is evidence about the edge, not the credential.

The websocket-unavailable fallback machinery (transport-failure marker, 426
handshake denial, HTTP bridge raw-HTTP replay) already exists for connect
timeouts and 5xx upgrade rejections; the missing piece is classifying an
evidence-backed edge-challenge 403 as the same kind of transport failure.

## What changes

- Classify a WebSocket handshake 403 as an edge challenge only on explicit
  evidence: the authoritative `cf-mitigated: challenge` header, or a
  Cloudflare-identified HTML response whose body carries known challenge
  markers.
- A classified direct-connect edge challenge carries the existing websocket
  transport-failure provenance, so it rides the established recovery: surface
  without account penalty, arm the bounded handshake-denial marker (426), and
  degrade HTTP responses paths to the raw HTTP upstream.
- The automatic-transport raw streaming path retries once over HTTP when the
  websocket handshake is rejected with a classified edge challenge, in
  addition to the existing 426 trigger. Routed handshake failures preserve
  the challenge classification through `CodexTransportError` for the same
  in-request fallback.
- Structured permission errors, `ip_forbidden`, ordinary reverse-proxy 403
  responses, forced WebSocket mode, and hard-pinned requests retain their
  existing fail-closed behavior.

## Scope

No database migration, new required setting, firewall relaxation, or host
Codex configuration change is introduced. Routed-proxy handshake challenges do
not arm the instance-wide marker: routed evidence stays route/account-scoped,
matching the existing transport-health design.
