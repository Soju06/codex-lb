# Serve h2c Upgrade Offers as Plain HTTP/1.1

## Why

JetBrains/Ktor clients attach opportunistic cleartext HTTP/2 upgrade headers
(`Connection: Upgrade, HTTP2-Settings` + `Upgrade: h2c` + `HTTP2-Settings`) to
ordinary HTTP/1.1 Responses API POSTs. The server's httptools-based HTTP parser
treats any such request as a protocol switch and wedges: a body coalesced with
the headers is silently dropped (the application validates an empty body and
returns 422), and a body written as a separate segment — Ktor's write pattern —
is answered with `400 Invalid HTTP request received.` before authentication
ever runs (issue #1757). RFC 9110 §7.8 allows a server to ignore an upgrade
offer and answer over HTTP/1.1, which is what upstream OpenAI endpoints do.

## What Changes

- Serve valid HTTP/1.1 requests that offer a non-WebSocket protocol switch
  (such as `h2c`) as normal HTTP/1.1 requests, with the full body delivered to
  the application for both client segmentations.
- Strip the declined offer's hop-by-hop headers (`Upgrade`, `HTTP2-Settings`,
  and their `Connection` tokens) before the request reaches the application.
- Keep genuine WebSocket upgrades switching protocols exactly as today.
- No new settings, budgets, or defaults; no API or schema change.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `http-ingress-limits`: raw HTTP ingress MUST serve non-WebSocket HTTP/1.1
  upgrade offers as plain HTTP/1.1 instead of dropping the body or rejecting
  the request.

## Impact

`app/cli.py` server bootstrap and new `app/core/http_protocol.py` /
`app/core/http_protocol_httptools.py` uvicorn protocol subclasses (the h11
variant covers the httptools-less fallback with the same header hygiene), plus
transport-level regression coverage. No dashboard, API, schema, or
configuration change.
