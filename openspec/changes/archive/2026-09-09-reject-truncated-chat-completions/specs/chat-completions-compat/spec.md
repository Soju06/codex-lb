## ADDED Requirements

### Requirement: Chat Completions reject truncated upstream Responses streams

`POST /v1/chat/completions` MUST classify upstream Responses iterator
exhaustion before a terminal `response.completed`, `response.incomplete`,
`response.failed`, or `error` event as `upstream_stream_truncated`. The error
MUST use OpenAI error type `server_error`. Partial content received before the
exhaustion MUST NOT be presented as a successfully completed non-streaming Chat
Completion.

#### Scenario: Streaming upstream EOF emits error and done

- **WHEN** a streaming Chat Completions request receives zero or more
  non-terminal upstream Responses events
- **AND** the upstream iterator reaches EOF before a terminal event
- **THEN** the proxy MUST emit an OpenAI error chunk with code
  `upstream_stream_truncated`
- **AND** the proxy MUST terminate the stream with `data: [DONE]`

#### Scenario: Collected upstream EOF returns an error envelope

- **WHEN** a non-streaming Chat Completions request receives zero or more
  non-terminal upstream Responses events
- **AND** the upstream iterator reaches EOF before a terminal event
- **THEN** the proxy MUST return HTTP 502
- **AND** the response body MUST be an OpenAI error envelope with code
  `upstream_stream_truncated` and type `server_error`
- **AND** the proxy MUST NOT return a `chat.completion` success object

#### Scenario: Explicit terminal events retain existing behavior

- **WHEN** the upstream iterator emits `response.completed`,
  `response.incomplete`, `response.failed`, or `error`
- **THEN** the proxy MUST preserve the existing Chat Completions mapping for
  that event
- **AND** the proxy MUST preserve existing usage, tool-call, and upstream
  generator cleanup behavior
