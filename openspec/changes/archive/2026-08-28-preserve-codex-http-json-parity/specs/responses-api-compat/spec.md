## ADDED Requirements

### Requirement: Backend non-streaming Responses preserve HTTP JSON transport

When `POST /backend-api/codex/responses` receives a valid request with
`stream: false`, the service MUST preserve that value in the upstream request,
MUST use upstream HTTP rather than WebSocket, and MUST return one
`application/json` Response object rather than an SSE stream. The service MUST
retain the existing account selection, error masking, usage settlement, and
request logging behavior around that request. This requirement does not change
the `/v1/responses` subscription compatibility path, which MAY aggregate an
upstream stream when required by the configured ChatGPT Codex backend.

#### Scenario: Backend stream false stays false upstream

- **GIVEN** a client sends `POST /backend-api/codex/responses` with
  `stream: false`
- **WHEN** codex-lb forwards the request to an HTTP-capable upstream
- **THEN** the upstream request body MUST contain `stream: false`
- **AND** its request headers MUST advertise `Accept: application/json`
- **AND** codex-lb MUST NOT open an upstream WebSocket for that request.

#### Scenario: Backend non-streaming response remains JSON downstream

- **GIVEN** the upstream returns a successful single Response JSON object
- **WHEN** codex-lb completes the backend non-streaming request
- **THEN** the downstream response MUST have an `application/json` content type
- **AND** its Response fields MUST preserve the upstream values.
