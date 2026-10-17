## MODIFIED Requirements

### Requirement: Backend non-streaming Responses preserve HTTP JSON transport

When `POST /backend-api/codex/responses` receives a valid request with
`stream: false`, the service MUST preserve that value in the upstream request,
MUST use upstream HTTP rather than WebSocket, and MUST return one
`application/json` Response object rather than an SSE stream. The service MUST
retain the existing account selection, error masking, usage settlement, and
request logging behavior around that request. This requirement does not change
the `/v1/responses` subscription compatibility path, which MAY aggregate an
upstream stream when required by the configured ChatGPT Codex backend.

When the completed HTTP exchange returns a canonical background acknowledgement
with `object = response`, a non-empty response ID without surrounding
whitespace, status `queued` or
`in_progress` matching the event type, and `output = []`, the proxy MUST treat
the transport as successful: the request log MUST use `status=success` without
`stream_incomplete`, account health MUST take the successful-request path, and
the account MUST NOT receive a transient error-health penalty. This transport
classification MUST NOT make malformed or partial response objects successful
and MUST NOT make `response.queued` or `response.in_progress` terminal for SSE
streams.

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

#### Scenario: Accepted background JSON settles successfully

- **GIVEN** a backend Responses request has `stream: false`
- **AND** upstream returns one valid Response object with status `queued` or
  `in_progress`
- **AND** the object has a non-empty, unpadded ID and an empty output list
- **WHEN** codex-lb finishes reading that HTTP response
- **THEN** the Response object is returned unchanged
- **AND** the request log records success without `stream_incomplete`
- **AND** the request log stores the returned response ID for later owner lookup
- **AND** account health records success without an error penalty

#### Scenario: Malformed background object retains error settlement

- **GIVEN** a backend non-streaming response reports queued or in-progress
- **BUT** its response object is missing canonical acknowledgement fields or
  contains malformed output items
- **WHEN** codex-lb validates the response
- **THEN** the external contract error is returned
- **AND** request-log and account-health settlement MUST remain on the error
  path

#### Scenario: Streaming progress EOF remains truncated

- **GIVEN** a Responses request uses streaming transport
- **AND** upstream emits `response.queued` or `response.in_progress`
- **WHEN** the stream ends before a terminal Responses event
- **THEN** settlement remains `stream_incomplete`
- **AND** existing request-log and account-health error handling remains
  unchanged
