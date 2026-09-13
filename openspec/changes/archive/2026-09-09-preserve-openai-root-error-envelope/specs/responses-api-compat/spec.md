## ADDED Requirements

### Requirement: OpenAI error path families include their exact roots

Locally generated HTTP errors for exact `/v1` and `/backend-api` requests MUST
use the same OpenAI-compatible external error envelope as requests under
`/v1/` and `/backend-api/`. Exact roots, trailing-slash roots, and unknown child
paths MUST preserve equivalent HTTP status, error type, error code, and message
semantics. This classification MUST NOT change dashboard, static-asset, health,
or other non-OpenAI route error formats.

#### Scenario: Exact OpenAI family roots are not found

- **WHEN** a client sends `GET /v1` or `GET /backend-api`
- **THEN** the service returns HTTP 404
- **AND** the body is an OpenAI error envelope with
  `error.type = invalid_request_error`, `error.code = not_found`, and
  `error.message = Not Found`

#### Scenario: Equivalent OpenAI family paths remain consistent

- **WHEN** a client requests a trailing-slash root or unknown child under
  `/v1/` or `/backend-api/`
- **THEN** the service returns the same 404 OpenAI error contract as the exact
  family root

#### Scenario: Non-OpenAI routes retain their native error formats

- **WHEN** a request fails on a dashboard, static-asset, health, or other
  non-OpenAI route
- **THEN** the service retains that route family's existing external error
  format
