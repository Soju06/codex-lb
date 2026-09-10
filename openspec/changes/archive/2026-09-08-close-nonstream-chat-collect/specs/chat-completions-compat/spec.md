## ADDED Requirements

### Requirement: Non-streaming chat collect closes the upstream generator

When `stream` is `false` or omitted, `POST /v1/chat/completions` MUST close the upstream Responses generator after collect returns or raises, including when the first consumed event is `response.failed` or `error`. Closing MUST run the generator finalizer so an open API-key reservation is released or settled before the HTTP response is returned.

#### Scenario: First-event rate limit releases the reservation

- **WHEN** the startup probe did not consume the stream
- **AND** the first upstream event is `response.failed` with
  `code=rate_limit_exceeded`
- **AND** the request reserved API-key usage
- **THEN** the reservation is released before the error response is returned

### Requirement: Non-streaming chat errors use the Responses status map

When non-streaming `POST /v1/chat/completions` returns an OpenAI error envelope collected from the upstream Responses stream, the HTTP status MUST match the non-streaming `/v1/responses` mapping for that envelope (`429` for `rate_limit_exceeded`, `503` for unavailable-selection codes, `401`/`400` where that path already maps them). The envelope body MUST remain an OpenAI error object.

#### Scenario: Collected rate limit is 429

- **WHEN** non-streaming chat collect returns
  `{ "error": { "code": "rate_limit_exceeded", ... } }`
- **THEN** the HTTP status is `429`
- **AND** the body is that OpenAI error envelope
