## Context

The request-decompression middleware currently reads the complete encoded body before it applies the configured decompressed-size limit. Requests without `Content-Encoding` bypass that middleware entirely, so neither path bounds raw bytes before FastAPI parses the body. The same middleware constructs dashboard error envelopes directly even when the request targets an OpenAI-compatible proxy path.

The implementation must protect body parsing that occurs before route dependencies, retain the existing 32 MiB general and 128 MiB Responses defaults, avoid a new operator setting, and remain independent from future multipart resource controls.

## Goals / Non-Goals

**Goals:**

- Bound raw HTTP bytes incrementally before an over-limit chunk reaches downstream body parsing.
- Continue bounding expanded bodies after supported request decompression.
- Preserve the larger Responses HTTP budget and exact-boundary behavior.
- Return the established dashboard or OpenAI-compatible error envelope for the request path.
- Preserve client-disconnect semantics and avoid whole-body prebuffering in the new guard.

**Non-Goals:**

- Multipart per-file, aggregate-file, or parser limits.
- Websocket ingress, upstream `response.create` slimming, or reverse-proxy configuration.
- New `CODEX_LB_*` settings or changes to the existing budget defaults.

## Decisions

### Add a pure ASGI raw-body guard outside request decompression

Register a pure ASGI middleware immediately after the request-decompression middleware. Starlette's registration order makes the new guard outer to decompression, so compressed bytes are bounded before decompression reads the body, while the existing bulkhead and other outer middleware still apply first.

The guard wraps `receive`, counts each `http.request` body's actual bytes, and raises before returning the chunk that crosses the limit. A valid over-limit `Content-Length` is rejected before calling the downstream application; missing, malformed, or understated values still use streamed counting. Exact-limit requests remain valid. This avoids a second whole-body buffer and covers chunked transfers.

Starlette redirects unmatched trailing-slash routes without consuming a chunked body. Register hidden trailing-slash aliases for both Responses HTTP routes so those documented equivalent paths enter the same typed-body parsing and ingress flow instead of returning 307 before the streamed limit is exercised.

Alternatives rejected:

- Route dependencies run after FastAPI can parse request bodies and therefore cannot protect pre-authentication parsing.
- A `BaseHTTPMiddleware` implementation adds request-stream adaptation without providing value here; the ASGI receive contract is the boundary being limited.
- A server-global cap cannot preserve the larger Responses budget and does not replace the expanded-body cap.

### Reuse the existing route budgets and leave plain multipart unchanged

The raw guard and decompressor share one path-budget selector. `/v1/responses` and `/backend-api/codex/responses`, after removing trailing slashes, use the larger of `max_decompressed_body_bytes` and `max_decompressed_responses_body_bytes`; other guarded paths use `max_decompressed_body_bytes`.

Requests declaring `multipart/form-data` without `Content-Encoding` bypass this whole-body guard. Encoded multipart remains guarded because decompression otherwise creates the same raw and expansion risks. Content type is client-declared, so this exemption is not a security boundary; multipart-specific aggregate, parser, and per-file limits remain a separate change with their own compatibility analysis.

### Use a private receive sentinel plus a request-scope marker

FastAPI reads body and form parameters before dependencies. With the installed FastAPI and Starlette middleware stack, a receive exception raised through the inner request-decompression `BaseHTTPMiddleware` is converted into a framework HTTP 400 even when the original exception is an `HTTPException`. The receive wrapper therefore sets a private request-scope marker and raises a private sentinel exception when the limit is crossed.

The existing framework HTTP-exception handler checks that marker before normal 400 formatting and restores the intended 413 for typed body/form parsing. The raw ASGI middleware catches only the exact sentinel when it escapes directly, including overflow while decompression reads the body outside FastAPI's `ExceptionMiddleware`; it emits the 413 only if no response has started. Client disconnects, cancellation, and unrelated receive failures pass through unchanged.

### Centralize path-sensitive ingress error construction

Ingress occurs before route metadata can reliably set `request.state.error_format`, so error format is determined from the stable path families. `/v1/*`, `/backend-api/*`, `/api/codex/*`, and `/internal/bridge/*` receive OpenAI-compatible envelopes after outer path canonicalization. Other paths retain the existing dashboard-shaped ingress envelope. Both raw overflow and decompression errors use the same constructor:

- overflow: HTTP 413 with `code = payload_too_large`;
- malformed or unsupported compression: HTTP 400, using `invalid_request_error` for OpenAI paths and `invalid_request` otherwise.

OpenAI ingress errors use `type = invalid_request_error`. Existing decompression support for `gzip`, `deflate`, `zstd`, `identity`, and stacked encodings remains unchanged after the raw guard. Stacked encodings are removed in reverse header/application order, and every intermediate decoded representation remains capped.

## Risks / Trade-offs

- **The 128 MiB Responses budget still permits substantial per-request memory use** → Existing bulkhead/backpressure middleware remains outside the guard, and both raw and expanded representations are independently bounded by the established budget.
- **A client can lie in `Content-Length`** → The receive wrapper always counts actual chunks even after a declared length passes the early check.
- **A downstream component could start a response before reading a later body chunk** → The raw middleware tracks `http.response.start` and re-raises rather than attempting a second response.
- **A client can label an unencoded body as multipart to bypass the generic guard** → The declared-media-type exemption is explicit and tested; multipart aggregate/parser/per-file controls are the standalone follow-up that closes this residual gap.

## Migration Plan

No data or configuration migration is required. Deploy through the normal rolling process and verify bounded dashboard and proxy requests. Rollback consists of reverting the middleware registration and associated error-format changes; existing settings remain compatible in both directions.

## Open Questions

None.
