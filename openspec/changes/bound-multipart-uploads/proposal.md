## Why

The account-import, transcription, and image-edit multipart routes accept unbounded file parts. FastAPI currently parses and spools those parts before route authorization, and the handlers then read the complete files into memory, allowing unauthenticated disk pressure and authenticated disk or heap exhaustion.

## What Changes

- Parse the four multipart upload surfaces only after their existing dashboard or proxy authorization dependencies have succeeded.
- Enforce fixed, zero-configuration limits against declared and actually streamed multipart bytes, individual file parts, text parts, part counts, and route-specific aggregate image bytes.
- Limit account imports to one 1 MiB `auth_json` file inside a 2 MiB multipart request.
- Limit transcription uploads to one 25,000,000-byte audio file inside a 32 MiB multipart request.
- Limit image edits to 16 source images plus one mask, with less than 50,000,000 binary bytes inside a 64 MiB multipart request.
- Return path-family HTTP 413 `payload_too_large` errors for byte-limit failures while retaining established validation behavior for malformed or missing parts.
- Close multipart spool files before database or upstream work and on every parsing, disconnect, cancellation, or limit-failure path.
- Reject compressed multipart bodies on these routes before the decompression layer can prebuffer them; `identity` remains a no-op.

## Capabilities

### New Capabilities

- `account-import`: Dashboard `auth_json` multipart import shape, authorization order, resource limits, cleanup, and error contract.

### Modified Capabilities

- `audio-transcriptions-compat`: Add bounded multipart parsing and upload limits to both native and OpenAI-compatible transcription routes.
- `images-api-compat`: Add bounded multipart parsing, source-image and mask count limits, and binary aggregate limits to image edits.
- `http-ingress-limits`: Restrict multipart exemptions to exact route-owned bounded operations while keeping every unrelated request under generic admission.

## Impact

The change affects the shared multipart parsing layer, request middleware ordering, account import, transcription, image edits, framework error mapping, OpenAPI request-body descriptions, and their unit/integration coverage. It adds no runtime setting or dependency. When composed with the generic raw HTTP ingress guard, the four route-owned multipart operations retain authorization and their dedicated body budgets as the admission boundary; every other multipart request retains generic raw admission, including unencoded bodies carrying a spoofed multipart media type.
