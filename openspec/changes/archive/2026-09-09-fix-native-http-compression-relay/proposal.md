## Why

The native HTTP egress helper can relay compressed response bytes and encoded-entity headers to Python adapters that expect decoded JSON or SSE. The initial decoder fix also removed the caller's `Accept-Encoding` and let reqwest synthesize its own value, which changed a traffic-identity header even when the caller sent none. The helper must fix response decoding without changing request compression negotiation.

## What Changes

- Preserve absence of `Accept-Encoding` by selecting a pooled reqwest client whose automatic decoders and generated compression advertisement are disabled.
- Preserve a present inbound `Accept-Encoding` value and enable reqwest decoding for gzip, deflate, Brotli, and zstd responses.
- Relay decoded response bytes with headers that describe the decoded representation.
- Cover absent-when-absent, present-when-present, and decoded gzip relay behavior at the native helper boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: native HTTP egress preserves caller compression-negotiation presence and value while keeping relayed response bytes and content-encoding metadata internally consistent.

## Impact

The native HTTP client-pool key, the `codex-lb-egress` reqwest feature set, native helper protocol regressions, and the internal outbound HTTP transport contract are affected. Public request and response shapes, routing, retry ownership, WebSocket compression, settings, and deployment steps are unchanged.
