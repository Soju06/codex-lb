## MODIFIED Requirements

### Requirement: Backend non-streaming Responses preserve HTTP JSON transport

When `POST /backend-api/codex/responses` receives a valid request with `stream: false`, the service MUST preserve that value upstream, MUST use HTTP rather than WebSocket, and MUST return one `application/json` Response object.

When the completed HTTP exchange returns a canonical background acknowledgement
with `object = response`, a non-empty response ID, status `queued` or
`in_progress` matching the event type, and `output = []`, the proxy MUST treat
the transport as successful: the request log MUST use `status=success` without
`stream_incomplete`, account health MUST take the successful-request path, and
the account MUST NOT receive a transient error-health penalty. This transport
classification MUST NOT make malformed or partial response objects successful
and MUST NOT make `response.queued` or `response.in_progress` terminal for SSE
streams.

#### Scenario: Accepted background JSON settles successfully

- **GIVEN** a backend Responses request has `stream: false`
- **AND** upstream returns one valid Response object with status `queued` or
  `in_progress`
- **AND** the object has a non-empty ID and an empty output list
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
