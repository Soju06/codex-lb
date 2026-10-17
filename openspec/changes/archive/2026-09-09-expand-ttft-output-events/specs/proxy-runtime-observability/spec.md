## MODIFIED Requirements

### Requirement: Websocket responses capture request-log latency timings

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. First-token latency MUST use the first token-bearing output delta, including text, refusal, reasoning-summary, function-call argument, custom-tool input, and tool-call output deltas, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event only when the item contains meaningful tool-call payload content and the tool protocol does not stream argument deltas. Recording MUST NOT change routing, failover, or the bytes returned to the client.

#### Scenario: Websocket text response records latency timings
- **GIVEN** a websocket responses request whose upstream emits a `response.created` event, then a text delta, then completion
- **WHEN** the proxy persists the request log
- **THEN** the log has non-null first-upstream-event, response-created, and first-token latency values
- **AND** first-upstream-event latency is less than or equal to response-created latency, which is less than or equal to first-token latency

#### Scenario: Websocket tool call records first-token latency
- **GIVEN** a websocket responses request whose first token-bearing output is a function-call argument delta, custom-tool input delta, tool-call output delta, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event with meaningful tool-call payload content when the tool protocol does not stream argument deltas
- **WHEN** the proxy persists the request log
- **THEN** the log has a non-null first-token latency value
- **AND** the proxy forwards the upstream event unchanged

#### Scenario: Control events do not record first-token latency
- **GIVEN** a responses request whose upstream has emitted only control events such as `response.created`
- **WHEN** the proxy inspects the request timing
- **THEN** first-token latency remains null until a token-bearing output delta arrives, unless a meaningful custom/apply-patch completion event anchors TTFT for a completion-only tool protocol
- **AND** a message, reasoning, or function-call `response.output_item.added` lifecycle event does not record first-token latency
- **AND** reasoning-summary placeholder deltas that are stripped before delivery do not record first-token latency
- **AND** metadata-only or empty tool-call delta and completion events do not record first-token latency

### Requirement: Dashboard request logs show generation speed

The dashboard request-log table MUST show time to first token and output-token generation speed when the required latency and output-token fields are available. Generation speed MUST use non-reasoning output tokens divided by elapsed generation time after time to first token, not total input plus output tokens and not total request latency including TTFT. When reasoning-token usage is unknown, it MUST be treated as zero for this metric. The displayed metric MUST remain named `TPS`.

#### Scenario: TPS excludes TTFT, input tokens, and reasoning tokens

- **GIVEN** a successful request log has 1,000 input tokens, 200 output tokens, including 40 reasoning tokens, 1,000 ms total latency, and 200 ms TTFT
- **WHEN** the dashboard renders request logs
- **THEN** it shows TTFT as 200ms
- **AND** it shows TPS as `(200 - 40) / 0.8 = 200.0`

#### Scenario: Unknown reasoning usage is treated as zero

- **GIVEN** a request has output tokens, valid total latency and TTFT, but no reasoning-token usage
- **WHEN** the dashboard calculates TPS
- **THEN** it uses the full output-token count as non-reasoning output

#### Scenario: missing speed inputs stay blank

- **GIVEN** a request log is missing TTFT, total latency, or output tokens
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a misleading calculated TPS value

#### Scenario: invalid speed inputs stay blank

- **GIVEN** a request log has output tokens and latency fields
- **AND** either total latency is less than or equal to TTFT or non-reasoning output tokens are zero or negative
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a calculated TPS value
