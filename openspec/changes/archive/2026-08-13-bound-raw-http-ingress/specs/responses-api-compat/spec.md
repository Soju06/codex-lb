## ADDED Requirements

### Requirement: Responses HTTP ingress uses the expanded bounded budget

HTTP requests to `/v1/responses` and `/backend-api/codex/responses`, including trailing-slash variants, MUST use the larger of `max_decompressed_body_bytes` and `max_decompressed_responses_body_bytes` as both the raw-body and decompressed-body ingress budget. The Responses-specific default MUST remain 128 MiB.

The trailing-slash variants MUST be hidden aliases of the canonical HTTP handlers rather than redirects, so streamed bodies receive the same admission, authorization, and route behavior.

If either representation exceeds that budget, the service MUST stop before route logic or upstream forwarding and return HTTP 413 with an OpenAI-compatible error envelope carrying `error.code = payload_too_large` and `error.type = invalid_request_error`.

This transport-ingress 413 applies before parsing and is distinct from the existing application-level oversized-`response.create` guard. A request that fits the 128 MiB transport budget but still exceeds the upstream websocket budget after historical slimming MUST retain the existing HTTP 400 `payload_too_large` behavior and `param = input`.

#### Scenario: Larger Responses request fits both ingress checks

- **WHEN** a Responses HTTP request is larger than the general budget but no larger than the Responses budget in either raw or decompressed form
- **THEN** the ingress guards allow the request to continue to Responses route handling

#### Scenario: Trailing-slash Responses request is admitted without redirect

- **WHEN** a client sends a chunked HTTP request to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** the service applies the same ingress budget and handler as the corresponding canonical path
- **AND** it does not return a trailing-slash redirect before consuming the guarded body

#### Scenario: Responses raw body exceeds its budget

- **WHEN** a Responses HTTP request's raw body exceeds the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Responses expanded body exceeds its budget

- **WHEN** an encoded Responses HTTP request fits the raw budget but expands beyond the Responses budget
- **THEN** the service returns HTTP 413 with `error.code = payload_too_large` and `error.type = invalid_request_error`
- **AND** the service does not invoke Responses route logic or forward the request upstream

#### Scenario: Post-slimming application rejection remains 400

- **WHEN** a Responses HTTP request fits the raw and decompressed transport-ingress budget
- **AND** its serialized `response.create` still exceeds the upstream websocket budget after historical slimming
- **THEN** the existing application-level guard returns HTTP 400 with `error.code = payload_too_large`, `error.type = invalid_request_error`, and `error.param = input`
