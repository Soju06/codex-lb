## Purpose

Keep the native helper's HTTP request identity and response representation equivalent to the Python transport used by the JSON and SSE adapters.

## Rationale

Inbound requests can include a first-party `Accept-Encoding` value or omit the header entirely. Both states are traffic-significant: a present value must reach the origin unchanged, while an absent value must not be replaced by a reqwest-generated default. When compression is negotiated, the native helper owns decoding before passing response bytes to parsers that consume JSON or SSE. The relayed headers must describe those same bytes; forwarding an encoding marker with decoded bytes, or removing the marker while forwarding compressed bytes, is internally inconsistent.

## Constraints

- Decoding remains streaming and owned by reqwest.
- Inbound compression-negotiation presence and value are preserved across the native HTTP boundary.
- The Python IPC adapter continues to perform only base64 transport decoding.
- Routing, request replay eligibility, cancellation ownership, and WebSocket extension negotiation remain unchanged.

## Failure Mode

Without decoder support, a valid compressed response reaches Python as framing bytes rather than `{` or `data:`, causing JSON or SSE parsing to fail after a successful upstream response. Without a separate no-decoder client policy, reqwest synthesizes `Accept-Encoding` on requests whose callers omitted it, changing the origin-visible identity header.

## Example

For a request with `Accept-Encoding: gzip` and an upstream `Content-Encoding: gzip` response whose decoded body is `{"status":"completed"}`, the origin observes the caller's header while the native helper relays the JSON byte sequence and omits the stale encoded-entity `Content-Encoding` and `Content-Length` headers. For a request without `Accept-Encoding`, the origin also observes no such header.
