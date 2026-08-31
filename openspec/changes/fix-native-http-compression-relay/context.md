## Purpose

Keep the native helper's HTTP response representation equivalent to the Python transport used by the JSON and SSE adapters.

## Rationale

Inbound requests can carry first-party `Accept-Encoding` values that name more
codings than the native helper compiles. The helper therefore removes inbound
compression negotiation and lets reqwest advertise only its enabled decoder.
Once gzip is advertised, the helper owns decoding that representation before
passing bytes to parsers that consume JSON or SSE. The relayed headers must
describe those same bytes; forwarding a gzip marker with decoded bytes, or
removing the marker while forwarding compressed bytes, is internally
inconsistent.

## Constraints

- Decoding remains streaming and owned by reqwest.
- Inbound compression negotiation is not forwarded across the native boundary.
- The Python IPC adapter continues to perform only base64 transport decoding.
- Routing, request replay eligibility, cancellation ownership, and WebSocket extension negotiation remain unchanged.

## Failure Mode

Without decoder support, a valid upstream gzip response begins with gzip
framing bytes rather than `{` or `data:`, causing JSON or SSE parsing to fail
after a successful upstream response. Forwarding unsupported codings creates
the same failure for Brotli, deflate, or zstd, so the helper must not advertise
them until their decoders are enabled.

## Example

For an upstream response with `Content-Encoding: gzip` whose decoded body is `{"status":"completed"}`, the native helper relays that JSON byte sequence and omits the stale encoded-entity `Content-Encoding` and `Content-Length` headers.
