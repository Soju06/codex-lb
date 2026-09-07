## Context

Native reqwest egress has compression codecs disabled. It preserves compressed
upstream body bytes, while aiohttp normally decodes them. The control response
header filter does not expose content encoding. Success status therefore does
not establish that downstream clients can decode a search response.

## Decision

Negotiate `Accept-Encoding: identity` in the shared control client before either
transport branch. Use the existing singleton-header replacement helper so a
native client's header position and spelling stay stable. A missing field is
appended once. This makes both transports receive the same opaque uncompressed
representation, including error envelopes. No successful response schema is
parsed or rewritten by production code.

Adding response decompression to all native HTTP streams would affect unrelated
model-discovery and SSE consumers; merely forwarding `Content-Encoding` would
double-decode responses already decoded by aiohttp. This change stays within
unary control negotiation and preserves the established downstream allowlist.

## Trade-off

Search result bodies use more upstream bandwidth (the diagnostic example was
roughly 69 KiB uncompressed). No client configuration is required. Servers must
honor HTTP identity negotiation; malformed or noncompliant upstream responses
are outside this correction.

## Validation

Public-route tests exercise the real control client through a loopback HTTP proxy
with raw response bytes, including the native response adapter. A server that
compresses only when requested reproduces the original JSON failure before the
fix. Exercise successful JSON, normalized error JSON, duplicate/case-varied
encoding headers, and direct/routed transport branches. Live validation must
decode a successful search and check its `output` and `results` fields.
