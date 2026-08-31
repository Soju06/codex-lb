## Why

The native HTTP egress helper forwards `Accept-Encoding` but currently relays compressed response bytes and encoded-entity headers to adapters that expect decoded JSON or SSE. Gzip responses therefore fail parsing even though the equivalent Python transport transparently decodes them.

## What Changes

- Decode supported upstream HTTP content codings in the native helper before relaying response chunks.
- Relay headers that describe the decoded representation rather than the encoded entity.
- Add a deterministic gzip regression at the native HTTP client boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: native HTTP egress keeps response body bytes and relayed content-encoding metadata internally consistent when compression is negotiated.

## Impact

The Rust workspace reqwest feature set, native HTTP egress regression coverage, and the internal outbound HTTP transport contract are affected. Public request and response shapes, routing, retry ownership, WebSocket compression, settings, and deployment steps are unchanged.
