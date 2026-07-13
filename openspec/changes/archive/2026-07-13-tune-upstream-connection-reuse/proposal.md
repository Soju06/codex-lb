## Why

The shared upstream connectors used aiohttp defaults: 15 s connection keepalive and 10 s DNS TTL. Interactive Codex turns are usually further apart than that, so nearly every turn paid a fresh DNS lookup plus TCP/TLS handshake to the upstream host (~100–300 ms of time-to-first-token) instead of reusing the pooled connection.

## What Changes

- Shared HTTP and websocket-handshake connectors set `keepalive_timeout=90` and `ttl_dns_cache=300`, so idle upstream connections and resolved names survive across interactive turns. No other connector behavior changes; SOCKS-proxied connectors are untouched (proxy connector library manages its own reuse).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `outbound-http-clients`: shared upstream connectors MUST keep idle connections and DNS results alive across typical interactive turn gaps.

## Impact

`app/core/clients/http.py` only; behavior-preserving beyond connection reuse timing.
