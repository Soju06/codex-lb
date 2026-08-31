## Context

The Python proxy forwards first-party `Accept-Encoding` values into the persistent Rust helper. The helper's reqwest build currently omits response decoder features, then emits upstream headers and raw body chunks over IPC. Python only base64-decodes those chunks, so compressed JSON and SSE reach parsers still encoded.

## Goals / Non-Goals

**Goals:**

- Keep native response bytes and relayed headers semantically consistent.
- Match the transparent response-decoding behavior of the existing Python egress path.
- Ensure the helper advertises only response codings compiled into its HTTP client.
- Preserve streaming delivery through reqwest rather than buffering complete responses.

**Non-Goals:**

- Preserve an inbound `Accept-Encoding` value that advertises unsupported codings.
- Add custom compression or decompression code.
- Change routing, replay, cancellation, WebSocket compression, or downstream public compression policy.

## Decisions

- Remove the inbound `Accept-Encoding` header at the Rust request boundary. reqwest then negotiates only decoders compiled into the native helper instead of forwarding caller codings it may not support.
- Enable reqwest's gzip response decoder in the shared workspace dependency. reqwest already owns HTTP framing and automatically removes `Content-Encoding` and encoded `Content-Length` when it decodes, so the existing header snapshot and chunk relay remain one coherent boundary.
- Prove the contract with a local gzip origin, a broad inbound coding list, and assertions on the origin's negotiated header, decoded body bytes, and removed encoded-entity headers. This catches unsupported-advertisement, raw-byte, and stale-header regressions.
- Limit compiled decoder support to the demonstrated gzip coding. The helper can add other decoders later with a focused regression for each without advertising them beforehand.

Alternative considered: compile every decoder named by arbitrary inbound values. Brotli, deflate, and zstd would increase binary and dependency surface without an executable contract for each coding. Removing inbound negotiation lets the helper advertise its actual capabilities while retaining gzip compression.

Alternative considered: manually decompress chunks in the bridge. Streaming decompression and header normalization would duplicate behavior already provided by reqwest and create a second codec/error seam.

## Risks / Trade-offs

- [Decoder support increases the native binary dependency surface] -> Enable only the demonstrated gzip feature and rely on reqwest's maintained decoder path.
- [Removing inbound negotiation could disable compression] -> Reqwest automatically advertises gzip because that decoder is compiled, which the real-helper QA verifies at the origin.
- [Decoded bytes could retain encoded-entity metadata] -> Assert both `Content-Encoding` and encoded `Content-Length` are absent in the regression and real-helper QA.
- [A malformed gzip body can fail during streaming] -> Retain reqwest's existing typed decode/body error classification.

## Migration Plan

No data or configuration migration is required. Deployment replaces the native helper binary. Rollback restores the previous binary and its previous compressed-response failure behavior.

## Open Questions

None.
