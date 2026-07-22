# Context: bounded raw HTTP ingress

## Purpose

This change closes the pre-routing memory-exhaustion gap for guarded HTTP requests. The existing decompression cap protects expanded data, but it runs only after the complete encoded body has been collected; unencoded JSON bodies have no equivalent ingress cap at all. Requests declaring unencoded multipart remain a documented residual gap for the standalone multipart-limits change.

## Decisions and constraints

- Reuse the two existing request-body settings and defaults; do not add another operator knob.
- Count actual ASGI body chunks so `Content-Length` is an optimization, not a trust boundary.
- Keep the larger budget for the two Responses HTTP aliases, including trailing slashes.
- Register trailing-slash HTTP paths explicitly so chunked bodies reach admission instead of being redirected before consumption.
- Limit raw bytes before decompression and expanded bytes after decompression.
- Keep requests declaring unencoded multipart outside this change. The declared media type is spoofable and is not a security boundary; file-aware aggregate, parser, and per-file limits need separate requirements and compatibility tests.
- Select the error envelope from the canonical path because the rejection can happen before router/dependency metadata is available. Proxy families include `/v1/*`, `/backend-api/*`, `/api/codex/*`, and `/internal/bridge/*`.
- Preserve endpoint authorization for every body admitted by the ingress guard; the transport guard is not an authentication replacement.

## Failure modes

- A missing or false `Content-Length` cannot bypass the limit because streamed bytes are counted.
- A single oversized chunk is rejected before downstream code receives that chunk.
- Malformed and unsupported compression stays a client error rather than reaching route logic.
- A syntactically malformed under-limit typed body can still fail parsing before router-level authorization; the ingress guard does not change dependency ordering.
- A disconnect remains a disconnect; it is not converted into a payload-size response.
- If a response has already started, the middleware does not attempt to write a competing 413 response.

## Example

With a 32 MiB general budget, a chunked JSON upload can deliver chunks totaling exactly 32 MiB. The next non-empty byte raises the ingress limit signal before FastAPI sees that chunk and returns HTTP 413. The same request under `/v1/*` receives an OpenAI error object with `type = invalid_request_error`; under an ordinary dashboard `/api/*` path it receives the dashboard error object.
