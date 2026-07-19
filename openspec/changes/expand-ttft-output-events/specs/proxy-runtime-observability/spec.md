## MODIFIED Requirements

### Requirement: Websocket responses capture request-log latency timings

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. First-token latency MUST use the first token-bearing output delta, including text, refusal, reasoning-summary, function-call argument, and tool-call output deltas, or a custom/apply-patch tool-call `response.output_item.added` event when the tool protocol does not stream argument deltas. Recording MUST NOT change routing, failover, or the bytes returned to the client.

#### Scenario: Websocket text response records latency timings

- **GIVEN** a websocket responses request whose upstream emits a `response.created` event, then a text delta, then completion
- **WHEN** the proxy persists the request log
- **THEN** the log has non-null first-upstream-event, response-created, and first-token latency values
- **AND** first-upstream-event latency is less than or equal to response-created latency, which is less than or equal to first-token latency

#### Scenario: Websocket tool call records first-token latency

- **GIVEN** a websocket responses request whose first token-bearing output is a function-call argument delta, tool-call output delta, or custom/apply-patch tool-call `response.output_item.added` event
- **WHEN** the proxy persists the request log
- **THEN** the log has a non-null first-token latency value
- **AND** the proxy forwards the upstream event unchanged

#### Scenario: Control events do not record first-token latency

- **GIVEN** a responses request whose upstream has emitted only control events such as `response.created`
- **WHEN** the proxy inspects the request timing
- **THEN** first-token latency remains null until a token-bearing output delta arrives
- **AND** a message, reasoning, or function-call `response.output_item.added` lifecycle event does not record first-token latency
- **AND** reasoning-summary placeholder deltas that are stripped before delivery do not record first-token latency
- **AND** metadata-only or empty tool-call delta events do not record first-token latency

### Requirement: Dashboard TPS excludes reasoning tokens

The dashboard request-log table and Reports median TPS MUST divide non-reasoning output tokens by elapsed generation time after TTFT. The displayed metric MUST remain named `TPS`.

#### Scenario: Reasoning tokens are excluded from TPS

- **GIVEN** a request has 200 output tokens, including 40 reasoning tokens, 1,000 ms total latency, and 200 ms TTFT
- **WHEN** the dashboard calculates TPS
- **THEN** it displays `(200 - 40) / 0.8 = 200.0` TPS
- **AND** Reports uses the same per-request numerator for daily median TPS
