## ADDED Requirements

### Requirement: Raw HTTP request ingress is bounded incrementally

The service MUST enforce the applicable request-body budget against actual raw bytes received for each guarded HTTP request. It MUST reject the request before exposing a chunk that would make the cumulative raw body exceed the budget, and it MUST NOT prebuffer the complete body solely to enforce this limit.

#### Scenario: Declared oversized body is rejected before downstream parsing

- **WHEN** a guarded HTTP request declares a valid `Content-Length` greater than its applicable budget
- **THEN** the service returns HTTP 413 without invoking downstream request-body parsing

#### Scenario: Chunked body crosses the budget

- **WHEN** a guarded HTTP request has no usable `Content-Length` and its received chunks cumulatively exceed the applicable budget
- **THEN** the service returns HTTP 413
- **AND** the chunk that crosses the budget is not exposed to downstream body parsing

#### Scenario: Exact-boundary body is accepted by the ingress guard

- **WHEN** a guarded HTTP request's actual raw body size equals its applicable budget
- **THEN** the raw ingress guard allows the complete body to continue downstream

#### Scenario: Client disconnect remains a disconnect

- **WHEN** the ASGI server reports `http.disconnect` while a guarded body is being received
- **THEN** the ingress guard propagates the disconnect without converting it into an HTTP 413 response

### Requirement: HTTP ingress reuses existing budgets

The service MUST use `max_decompressed_body_bytes` as the general raw and decompressed HTTP request-body budget. When an owning route capability defines a larger budget from an existing route-specific setting, the ingress guard MUST use that route budget. The HTTP ingress guard MUST NOT add another setting or change existing defaults.

Route-specific budget and error-envelope selection MUST use the application-relative route path after removing any matching ASGI `root_path` prefix.

Requests declaring `multipart/form-data` without `Content-Encoding` MUST remain outside this generic whole-body guard. Multipart requests carrying `Content-Encoding` MUST remain guarded. The service MUST NOT treat the client-declared multipart media type as a trusted security boundary.

#### Scenario: Another HTTP path uses the general budget

- **WHEN** a guarded request targets any other HTTP path
- **THEN** its raw and decompressed HTTP ingress budget is `max_decompressed_body_bytes`

#### Scenario: Declared unencoded multipart remains unaffected

- **WHEN** a request declares media type `multipart/form-data` and has no `Content-Encoding`
- **THEN** the generic raw whole-body guard does not reject it based on these budgets

#### Scenario: Encoded multipart remains guarded

- **WHEN** a `multipart/form-data` request carries a `Content-Encoding` header
- **THEN** the service applies both the raw and decompressed budget checks

#### Scenario: Mounted Responses route keeps its route-specific policy

- **GIVEN** the service is mounted under a non-empty ASGI `root_path`
- **WHEN** the request scope path includes that prefix and targets `/v1/responses` relative to the application
- **THEN** the service applies the Responses-specific ingress budget
- **AND** any ingress failure uses the OpenAI-compatible error envelope

### Requirement: Encoded HTTP bodies are bounded before and after decompression

For request bodies using `gzip`, `deflate`, `zstd`, `identity`, or supported stacked `Content-Encoding` values, the service MUST enforce the applicable budget independently against the encoded raw body and every intermediate and final decoded representation. The service MUST remove stacked encodings in reverse header/application order. Unsupported encodings or malformed compressed bodies MUST fail with HTTP 400.

#### Scenario: Encoded raw body exceeds the budget

- **WHEN** an encoded request's raw bytes exceed the applicable budget before decompression
- **THEN** the service returns HTTP 413 before attempting to hold an unbounded encoded body

#### Scenario: Expanded body exceeds the budget

- **WHEN** an encoded request is within the raw budget but expands beyond the applicable decompressed budget
- **THEN** the service returns HTTP 413

#### Scenario: Supported stacked encoding remains compatible

- **WHEN** a request uses a valid supported stack of `gzip`, `deflate`, `zstd`, or `identity` encodings and both representations fit the budget
- **THEN** the service decodes the body in reverse header/application order, caps every intermediate representation, and continues request handling

#### Scenario: Invalid compression is rejected

- **WHEN** a request uses an unsupported content encoding or carries malformed compressed bytes
- **THEN** the service returns HTTP 400 without invoking route logic

### Requirement: HTTP ingress failures use the path-family error envelope

Ingress failures on `/v1/*`, `/backend-api/*`, `/api/codex/*`, and `/internal/bridge/*` MUST use an OpenAI-compatible error envelope with `type = invalid_request_error`. Equivalent paths MUST be classified after the existing outer path canonicalization. Other ingress paths MUST retain the dashboard-compatible error envelope. Oversized requests MUST use `code = payload_too_large`; malformed or unsupported compression MUST use `code = invalid_request_error` on OpenAI paths and `code = invalid_request` on other paths.

#### Scenario: OpenAI path rejects an oversized body

- **WHEN** a raw or decompressed request body on an OpenAI-compatible proxy path exceeds its budget
- **THEN** the service returns HTTP 413
- **AND** the response has OpenAI error `code = payload_too_large` and `type = invalid_request_error`

#### Scenario: OpenAI path rejects invalid compression

- **WHEN** a request on an OpenAI-compatible proxy path uses unsupported or malformed compression
- **THEN** the service returns HTTP 400
- **AND** the response has OpenAI error `code = invalid_request_error` and `type = invalid_request_error`

#### Scenario: Dashboard settings path rejects an oversized body

- **WHEN** a raw or decompressed request body on `/api/settings` exceeds its budget
- **THEN** the service returns HTTP 413
- **AND** the response has dashboard error `code = payload_too_large`

#### Scenario: Dashboard settings path rejects invalid compression

- **WHEN** a request on `/api/settings` uses unsupported or malformed compression
- **THEN** the service returns HTTP 400
- **AND** the response has dashboard error `code = invalid_request`

#### Scenario: Duplicated Codex alias is classified after canonicalization

- **WHEN** an ingress failure targets `/backend-api/codex/v1/responses/`
- **THEN** the service applies the same Responses budget and OpenAI-compatible envelope as `/backend-api/codex/responses/`

### Requirement: Ingress admission preserves endpoint authorization

The HTTP ingress guard MUST NOT authenticate callers or replace, bypass, or relocate existing dashboard, proxy API-key, ChatGPT-identity, or internal-bridge authorization. Requests that reach dependency resolution MUST continue through the endpoint's existing authorization path. Existing FastAPI parsing order remains unchanged, so ingress rejection or syntactically invalid typed bodies can fail before router-level authorization.

#### Scenario: Admitted unauthenticated request still reaches proxy authorization

- **WHEN** a syntactically valid under-limit request without required credentials targets an API-key-protected proxy route
- **THEN** the ingress guard allows normal routing to continue
- **AND** the existing proxy authorization rejects the request with its established authentication response

#### Scenario: Declared oversized request fails before authorization

- **WHEN** a request declares a body larger than its ingress budget
- **THEN** the service returns the deterministic ingress 413 without invoking router-level authorization
