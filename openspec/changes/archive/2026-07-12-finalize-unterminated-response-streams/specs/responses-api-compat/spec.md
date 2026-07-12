## ADDED Requirements

### Requirement: Raw Responses streams require a terminal SSE event for success

For raw HTTP streaming Responses attempts, the proxy MUST NOT record request-log
status `success` or mark the selected account successful unless the stream
observed a terminal SSE event: `response.completed`, `response.failed`,
`response.incomplete`, or `error`. This requirement applies even when the
upstream HTTP response status was 200 because the stream body remains part of
the request outcome.

If the upstream iterator ends before a terminal event, the proxy MUST surface a
terminal `response.failed` SSE event with error code `stream_incomplete`, record
the request-log row as an upstream `stream_incomplete` error, and apply the
normal transient upstream account-health signal. If the downstream client
cancels or disconnects before a terminal event, the proxy MUST record the
request-log row as a downstream `client_disconnected` error and MUST NOT
penalize the upstream account.

#### Scenario: Raw stream upstream EOF is not successful

- **GIVEN** a raw HTTP streaming Responses request has emitted non-terminal SSE
  data
- **WHEN** the upstream stream ends before `response.completed`,
  `response.failed`, `response.incomplete`, or `error`
- **THEN** the downstream stream receives a terminal `response.failed` event
  with error code `stream_incomplete`
- **AND** the request log stores status `error`, error code
  `stream_incomplete`, and upstream failure metadata
- **AND** the selected account receives a transient upstream failure signal

#### Scenario: Raw stream downstream cancellation is client-side

- **GIVEN** a raw HTTP streaming Responses request has not observed a terminal
  SSE event
- **WHEN** the downstream client cancels or disconnects from the stream
- **THEN** the request log stores status `error`, error code
  `client_disconnected`, and downstream failure metadata
- **AND** the selected account is not penalized for the client-side close
