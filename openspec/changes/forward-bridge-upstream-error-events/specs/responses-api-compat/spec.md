### Requirement: HTTP bridge upstream terminal errors remain streaming events

For streaming HTTP bridge Responses requests, upstream terminal `response.failed`
and `error` frames MUST be forwarded as SSE stream events instead of being
converted into pre-stream HTTP JSON errors solely because they are the first
frame observed by the route startup probe. Public `/v1/responses` normalization
MUST still synthesize a leading `response.created` event when needed so OpenAI
SDK stream parsers can consume the terminal event. True local startup exceptions
raised before any upstream SSE frame MAY still be returned as non-streaming HTTP
error responses.

#### Scenario: bridge context-limit failure is streamed

- **GIVEN** a streaming `/v1/responses` request uses the HTTP responses bridge
- **WHEN** the first upstream frame is `response.failed` with
  `error.code = "context_length_exceeded"`
- **THEN** the downstream SSE stream contains the upstream `response.failed`
  event
- **AND** the public stream emits `response.created` before `response.failed`
- **AND** the route startup probe does not convert the upstream event into an
  HTTP JSON error response

#### Scenario: bridge overload failure is streamed

- **GIVEN** a streaming `/v1/responses` request uses the HTTP responses bridge
- **WHEN** the first upstream frame is `response.failed` or `error` with
  `error.code = "overloaded_error"`
- **THEN** the downstream SSE stream contains an SSE terminal error event that
  preserves `overloaded_error`
