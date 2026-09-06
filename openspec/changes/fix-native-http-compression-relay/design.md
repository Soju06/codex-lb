## Context

The Python proxy forwards each first-party `Accept-Encoding` value into the persistent Rust helper. The helper must preserve that request identity while ensuring that any compressed representation it negotiates is decoded before crossing IPC, because the Python adapter only base64-decodes native response chunks.

Reqwest automatically inserts `Accept-Encoding` when a decoder feature is enabled and the request does not already contain the header. A single decoder-enabled client therefore changes requests whose callers omitted compression negotiation.

## Goals / Non-Goals

**Goals:**

- Preserve `Accept-Encoding` presence and value across the native HTTP boundary.
- Decode gzip, deflate, Brotli, and zstd responses before relaying bytes to Python.
- Keep decoded body bytes and relayed response headers semantically consistent.
- Preserve streaming delivery through reqwest rather than buffering complete responses.

**Non-Goals:**

- Add custom compression or decompression code.
- Add support for content codings outside gzip, deflate, Brotli, and zstd.
- Change routing, replay, cancellation, WebSocket compression, or downstream public compression policy.

## Decisions

- Add `decode_response` to the native HTTP `ClientPool` key. Its value is derived from whether the inbound request contains `Accept-Encoding`, so requests with and without compression negotiation cannot reuse clients with different decoder policy.
- Build the no-inbound-header client with reqwest's `no_gzip`, `no_brotli`, `no_deflate`, and `no_zstd` controls. Disabling every compiled decoder prevents reqwest from synthesizing `Accept-Encoding`, preserving absence at the origin.
- Restore ordinary forwarding of inbound `Accept-Encoding` in `forwarded_headers`. A present value crosses the helper boundary unchanged rather than being removed and replaced by reqwest's default.
- Enable reqwest's gzip, deflate, Brotli, and zstd features on `codex-lb-egress`. This matches the established Python HTTP path's supported coding set and lets reqwest decode every coding in the common client header before response chunks cross IPC. Cargo adds eight compression-specific transitive packages for Brotli and zstd; this bounded dependency increase is preferred over rewriting the client's token list because exact forwarding preserves the traffic-parity identity header and avoids new quality-value parsing behavior.
- Continue to rely on reqwest's streaming decoder and header normalization. When it decodes a response, reqwest removes the stale `Content-Encoding` and encoded `Content-Length` before the helper snapshots response headers and relays chunks.

Alternative considered: intersect the inbound token list with only gzip. Although this avoids the Brotli and zstd dependencies, it changes the caller's identity header and requires correct parsing and reconstruction of coding parameters. Enabling the established four-coding set preserves the inbound value instead.

Alternative considered: manually decompress chunks in the bridge. Streaming decompression and header normalization would duplicate behavior already provided by reqwest and create a second codec and error seam.

## Risks / Trade-offs

- [Additional decoders increase native binary and build dependency surface] -> Scope the feature set to the four codings already supported by the Python path and use reqwest's maintained implementations.
- [A no-header request could accidentally reuse a decoder-enabled client] -> Include decoder policy in the pool key and cover both key partitioning and the origin-observed header with regressions.
- [Decoded bytes could retain encoded-entity metadata] -> Keep assertions that `Content-Encoding` and encoded `Content-Length` are absent after gzip decoding.
- [A malformed compressed body can fail during streaming] -> Retain reqwest's existing typed decode/body error classification.

## Migration Plan

No data or configuration migration is required. Deployment replaces the native helper binary. Rollback restores the previous helper behavior.

## Open Questions

None.
