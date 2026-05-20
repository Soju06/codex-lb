# Proposal: harden-compact-upstream-headers

## Why

Legacy `/responses/compact` requests can inherit downstream client and transport headers before they are sent to the ChatGPT compact endpoint. A direct minimal compact request succeeds, while proxied compact failures have surfaced as Cloudflare `400 Bad Request` HTML responses. The proxy should treat compact as its own upstream contract and avoid forwarding downstream body-framing, compression, and client identity headers.

## What Changes

- Strip request body-framing and hop-by-hop headers from inbound proxy header filtering.
- Build compact upstream headers from internal credentials and account identity only.
- Preserve compact routing and payload behavior while avoiding downstream header fingerprints on the upstream compact call.

## Impact

- Reduces false compact `400 upstream_error` failures caused by proxied request shape.
- Keeps normal Responses and websocket paths on their existing header contracts.
- Adds regression coverage for compact service filtering and final upstream header construction.
