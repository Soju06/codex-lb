## ADDED Requirements

### Requirement: Responses SSE terminal delivery is traced

For `/v1/responses`-family HTTP SSE responses the proxy MUST record, once per response, whether a terminal SSE frame (`response.completed`, `response.failed`, `response.incomplete`, or `error`, identified on an `event:` line even when the frame is split across body chunks) was handed to the HTTP server's writer, and whether the connection had already been lost when it was. A terminal frame counts as handed over only once its complete block — from the `event:` line through the blank-line terminator — was handed over, and a chunk counts as handed over only once the server's `send` returned for it; a frame whose `event:` line went out but whose block was cut short by a disconnect, cancellation, or end of body MUST NOT be reported as a written terminal. The result MUST be exposed as the Prometheus counter `codex_lb_stream_terminal_delivery_total{surface, outcome}` with the closed outcome set `terminal_written`, `terminal_after_disconnect`, `exception_before_terminal`, `cancelled_before_terminal`, `ended_without_terminal`, and as one low-cardinality log line carrying the ingress request id, surface, outcome, terminal event type, chunk and byte counts, and the exception type name if any. The trace MUST NOT alter the forwarded bytes, MUST NOT emit a synthetic frame, MUST NOT change request-log status or settlement, and MUST degrade to a no-op when the Prometheus client is absent. The log line's request id is the ingress request id, which the streaming service stores in `request_logs.archive_request_id` (not `request_logs.request_id`, which holds the upstream response id once `response.created` was seen).

#### Scenario: Terminal frame handed to a live connection

- **WHEN** a Responses SSE stream writes a terminal frame and the connection has not been lost
- **THEN** the counter is incremented once with `outcome="terminal_written"`
- **AND** a later cancellation or exception does not change that outcome

#### Scenario: Connection lost before the terminal write

- **GIVEN** the client connection was lost while the stream was still in flight
- **WHEN** the stream subsequently writes its terminal frame
- **THEN** the counter is incremented once with `outcome="terminal_after_disconnect"`
- **AND** the request log still records the terminal outcome the proxy parsed from upstream

#### Scenario: Terminal frame split across chunks

- **GIVEN** the terminal frame's `event:` line and the rest of its block arrive in separate body chunks
- **WHEN** every chunk through the blank-line terminator is handed over on a live connection
- **THEN** the counter is incremented once with `outcome="terminal_written"`, committed by the chunk carrying the terminator
- **WHEN** the connection was lost after the `event:` line but before the chunk carrying the terminator was handed over
- **THEN** the counter is incremented once with `outcome="terminal_after_disconnect"`
- **WHEN** the body is cancelled after the `event:` line but before the terminator was handed over
- **THEN** the counter is incremented once with `outcome="cancelled_before_terminal"`
- **WHEN** the body ends (`more_body=false`) after the `event:` line without the terminator
- **THEN** the counter is incremented once with `outcome="ended_without_terminal"`

#### Scenario: Wrapper exception before the terminal frame

- **WHEN** the response body raises before any terminal frame was written
- **THEN** the counter is incremented once with `outcome="exception_before_terminal"`
- **AND** the exception propagates unchanged and no additional `http.response.body` message is sent

#### Scenario: Body ends unfinished or without a terminal frame

- **WHEN** the body iteration is cancelled (client disconnect or server-side task cancellation) before a terminal frame, including a cancellation that lands while the server's `send` of the terminal chunk is still waiting on write backpressure
- **THEN** the counter is incremented once with `outcome="cancelled_before_terminal"`
- **WHEN** the body iterator is exhausted and the final `more_body=false` message is sent without a recognised terminal frame
- **THEN** the counter is incremented once with `outcome="ended_without_terminal"`
