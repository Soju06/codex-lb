## 1. Raw ingress guard

- [x] 1.1 Implement the pure ASGI raw request-body limiter with early `Content-Length` rejection, streamed chunk counting, exact-boundary behavior, disconnect passthrough, and the unencoded multipart exemption.
- [x] 1.2 Implement the shared route-budget selector, path-family error-envelope constructor, overflow scope marker, and exact sentinel handling.
- [x] 1.3 Register and export the limiter so canonical path rewriting and outer admission middleware run before it while request decompression runs after it; add hidden trailing-slash Responses aliases so streamed admission is not bypassed by a redirect.

## 2. Decompression and framework errors

- [x] 2.1 Refactor request decompression to reuse the route budget and path-sensitive error response while preserving supported and stacked encodings.
- [x] 2.2 Teach the framework HTTP-exception handler to restore marked body overflows to HTTP 413 without changing unrelated body-parse errors.

## 3. Regression coverage

- [x] 3.1 Add raw ASGI unit coverage for declared and streamed overflow, exact limits, understated lengths, oversized chunks, passthrough scopes, disconnects, unrelated receive failures, response-start safety, multipart scope, route budgets, aliases, and middleware order.
- [x] 3.2 Extend decompression tests for raw and expanded limits across `identity`, `gzip`, `deflate`, `zstd`, stacked encodings, trailing-slash Responses routes, OpenAI/dashboard error envelopes, and unchanged post-slimming HTTP 400 behavior.
- [x] 3.3 Add integration coverage proving typed uncompressed request bodies are bounded before route dependencies while admitted requests preserve existing authorization behavior.

## 4. Verification

- [x] 4.1 Run strict OpenSpec validation and the targeted unit and integration suites.
- [x] 4.2 Run Ruff, type checking, architecture checks, and diff hygiene checks; resolve all findings.
- [x] 4.3 Verify implementation against the OpenSpec artifacts and review the final standalone diff.
