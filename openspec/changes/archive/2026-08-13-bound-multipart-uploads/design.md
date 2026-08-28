## Context

FastAPI 0.136.1 parses every declared `File`/`Form` body before it solves router or endpoint dependencies. Starlette 1.3.1 spools file parts after 1 MiB but applies `max_part_size` only to non-file fields, with defaults of 1,000 files and 1,000 fields. The affected handlers then call unbounded `UploadFile.read()`, and image edits create additional base64 and request-model copies.

The four affected surfaces have different compatibility contracts: dashboard `auth_json` import, native and OpenAI-compatible transcription, and OpenAI-compatible image edits. The Codex JSON image-edit alias and the direct-to-SAS files protocol are not multipart and are outside this change.

## Goals / Non-Goals

**Goals:**

- Bound temporary-disk, multipart parser, and handler heap exposure with deterministic zero-configuration limits.
- Authenticate before any multipart body is consumed or spooled.
- Count actual streamed bytes so chunked bodies and understated `Content-Length` values cannot bypass admission.
- Preserve successful upload semantics, source-model form forwarding, OpenAPI request-body descriptions, path-family error envelopes, disconnects, cancellation, and image observability.
- Release every temporary file before account persistence, usage reservation, account selection, or upstream forwarding.

**Non-Goals:**

- Add operator-tunable upload settings, change the Starlette spool threshold, inspect media contents, or restrict filename/MIME values.
- Limit the non-multipart Codex image alias or direct-to-SAS file uploads.
- Add a process-wide aggregate upload/concurrency budget.
- Add dedicated multipart parsers or route-specific upload budgets outside the four exact operations.

## Decisions

### Parse inside authorized handlers

The affected handlers will no longer declare FastAPI `File` or `Form` parameters. They will accept `Request`, let existing router and endpoint dependencies complete, and then call a shared bounded parser. Their OpenAPI multipart request bodies will be retained explicitly.

This is preferred over a custom `APIRoute` that pre-populates `request._form`: a pre-parser can bound each request but still spools unauthenticated data. It is also preferred over `request.form(max_part_size=...)`, because the pinned Starlette version does not apply that value to file parts.

### Use a shared counted Starlette parser

A core multipart module will wrap `request.stream()` with a cumulative body counter and subclass `MultiPartParser` to enforce file, aggregate-file, and text-part byte limits before the crossing bytes are queued for spool writes. Starlette's native parser continues to own boundary parsing and closes partially-created files on parser errors. A context manager closes successful `FormData` on every exit.

The helper will reject a valid declared `Content-Length` above the route budget, but it will always count actual chunks. Because Starlette closes partial files only for its own parser and OS errors, the wrapper will also close every parser-owned spool on any `BaseException` before re-raising `ClientDisconnect`, `CancelledError`, or another non-limit failure unchanged. Handlers will still perform a bounded `limit + 1` read as a defense-in-depth check, copy the required bytes/text values, and close the form before invoking service or upstream logic.

### Apply fixed protocol policies

| Surface | Multipart body | File bytes | Aggregate binary | Files | Fields | Text part |
|---|---:|---:|---:|---:|---:|---:|
| account import | 2 MiB | 1 MiB | 1 MiB | 1 | 0 | n/a |
| either transcription route | 32 MiB | 25,000,000 | 25,000,000 | 1 | 32 | 256 KiB |
| image edits | 64 MiB | 49,999,999 | 49,999,999 | 17 | 32 | 256 KiB |

Image file counts are additionally classified by field: `image` and `image[]` share a maximum of 16, `mask` has a maximum of one, and unknown file fields are invalid. The mask participates in both per-file and aggregate binary limits.

Fixed defaults avoid growing the `CODEX_LB_*` configuration surface. The audio cap uses an inclusive 25,000,000-byte local interpretation of the public 25 MB upload contract. The image cap implements the public “less than 50 MB” wording literally and applies the same strict ceiling to the aggregate because codex-lb retains all binaries and base64 copies simultaneously.

The image aggregate is a resource-safety ceiling, not a promise that every payload below it fits the default 15 MiB internal `response.create` transport. The existing downstream size guard remains authoritative after base64/JSON expansion and can reject a smaller admitted upload with its established `payload_too_large` behavior.

### Reject encoded multipart before decompression

A small pure-ASGI middleware, registered outside the existing decompression middleware, will recognize only `POST` on the four canonical application-relative paths, regardless of the declared media type. It will remove the `Content-Encoding` header from a copied scope and place a private unsupported-encoding marker on non-identity requests without reading the body. After existing route authorization succeeds, the handler checks that marker and returns the 400 response before multipart parsing. Unauthorized requests therefore retain 401/403 precedence, `identity` remains a no-op, compressed bytes never reach the decompression layer, and other methods keep their existing behavior.

### Compose route-owned admission with generic raw ingress

After outer path canonicalization, the effective production order is multipart content-encoding gate, generic raw-body guard, then request decompression. For either `identity` or a non-identity value, the exact-path gate removes the header and places an internal handled marker on the copied scope without reading the body. The generic raw guard honors that marker independently of the client-declared media type. `identity` therefore reaches the authorized bounded parser and its dedicated multipart body budget; for a non-identity encoding, the authorized handler rejects the unsupported marker before parsing.

This is a narrow route-owned exception even when the declared `Content-Length` exceeds the generic raw budget. The account, transcription, and image-edit contracts remain authoritative for their four exact `POST` operations. An unencoded multipart request bypasses generic admission only when the same exact operation predicate selects its dedicated bounded parser. Every request on every other route remains under generic raw admission regardless of its declared media type, and encoded requests also retain decompressed-body admission.

### Keep byte and shape errors distinct

Body, file, aggregate-binary, and text-part byte failures return HTTP 413 with `code = payload_too_large`. Proxy routes use `type = invalid_request_error` and attach the known file parameter when applicable; account import uses the dashboard envelope. Multipart syntax and part-count/shape errors remain HTTP 400, while missing required typed-equivalent parts retain the existing dashboard 422 or proxy 400 validation contract.

Non-identity multipart content encoding returns HTTP 400 with the path-family invalid-request envelope after authorization. Image-edit auth, encoding, and parser failures record exactly one bounded route-completion observation without parsing multipart solely to derive observability labels; pre-parse labels use `model = unknown` and `stream = false`.

## Risks / Trade-offs

- **[Private framework integration changes in a future Starlette release]** → Keep the parser subclass narrow and add version-near tests for callback ordering, file rollover, cleanup, disconnect, and cancellation.
- **[Legitimate large uploads begin failing]** → Use public/protocol-aligned file limits, document the stricter aggregate image budget in OpenSpec, and return deterministic 413 envelopes.
- **[Manual extraction drifts from prior FastAPI coercion]** → Reuse existing Pydantic validation, preserve multi-item order for source transcription fields, and add before/after contract regressions for missing, duplicate, bracketed, and malformed parts.
- **[64 MiB image requests can still amplify in memory]** → Enforce a 50,000,000-byte aggregate binary cap, close spools before base64 conversion, and retain the existing downstream `response.create` guard.
- **[Concurrent admitted uploads still multiply the per-request budget]** → Existing proxy/dashboard bulkheads remain the concurrency boundary; a global byte semaphore is a separate change.

## Migration Plan

No database or configuration migration is required. Deploy the parser, middleware, handler extraction, and tests together. Rollback is a code rollback; clients that receive the new 413 response can retry with a smaller file without server-side cleanup.

## Open Questions

None.
