## ADDED Requirements

### Requirement: Upstream completion timing is persisted independently

SSE, WebSocket and HTTP bridge request logs MUST capture each upstream event's observation time before asynchronous event processing. Nullable `latency_upstream_terminal_ms` MUST record receipt of the terminal event belonging to the final attempt, before settlement, gate/lease release, deferred health writes or downstream consumption. Existing `latency_ms` MUST retain its total request duration semantics. Requests without an observed terminal MUST NOT synthesize a terminal timestamp. Retry/failover attempts MUST NOT reuse an earlier failed terminal timestamp. Recording MUST NOT change forwarded response bytes, routing, account ownership, reservation settlement ordering or cleanup guarantees.

#### Scenario: Settlement latency does not change generation latency

- **GIVEN** TTFT is 500 ms and the upstream terminal event arrives at 1,000 ms
- **WHEN** local settlement takes another 2,000 ms
- **THEN** the recorded upstream terminal latency remains 1,000 ms while total request latency may include the settlement

#### Scenario: Bridge consumer delay does not change TTFT

- **GIVEN** the bridge receives an output event while its downstream consumer is delayed
- **WHEN** the request log is persisted
- **THEN** first-output timing uses upstream receipt time rather than queue consumption time

### Requirement: Output speed sample evidence is preserved

New subscription-backed streaming logs MUST persist `latency_first_output_ms`, the first observed non-reasoning content time relative to the existing attempt/request-state anchor, and `output_delta_count`, the count of observed nonempty non-reasoning output chunks. Text, refusal and actual tool arguments/input MUST qualify; reasoning, metadata-only lifecycle events and empty deltas MUST NOT. These fields MUST remain nullable for historical and unsupported-source logs. Existing TTFT MAY still include visible reasoning or supported tool-start events and MUST be described as gateway-observed first output rather than model-internal or client end-to-end timing.

#### Scenario: Reasoning precedes actual output

- **GIVEN** a reasoning summary arrives at 200 ms and first text at 800 ms
- **WHEN** the request is logged
- **THEN** TTFT is 200 ms and first non-reasoning output latency is 800 ms
- **AND** TPS uses the non-reasoning output start

#### Scenario: Full terminal-only output

- **GIVEN** no streamed output has been observed and the terminal payload contains actual text or tool content
- **WHEN** that terminal event arrives
- **THEN** its receipt time may establish the first output and TTFT with one output chunk
- **AND** its TPS sample is insufficient
- **AND** positive usage alone MUST NOT synthesize first-output timestamps

### Requirement: Request generation speed exposes sample quality

The request-log API MUST expose nullable `generation_tps` and a `generation_tps_status` of `estimated`, `legacy_estimate`, `insufficient_sample`, `missing_usage`, `missing_timing`, `invalid_sample`, or `incomplete`. The UI MUST use this backend value rather than recalculate it. Estimated speed MUST use non-reasoning output tokens divided by time from first non-reasoning output to upstream completion. Only successful requests with known nonnegative output/reasoning counts, a positive non-reasoning count, valid timing order `0 <= TTFT <= first output <= upstream terminal <= total latency`, at least two output chunks and a window of at least 100 ms may receive `estimated`. The 100 ms boundary MUST be a sample qualification rule, not a speed cap or artificial denominator. Even qualified values MUST be presented as observed estimates rather than model-internal decode speed.

#### Scenario: Five-millisecond response is insufficient

- **GIVEN** ten synthetic non-reasoning output tokens and a five-millisecond output window
- **WHEN** speed is calculated
- **THEN** the API returns no numeric TPS and an insufficient-sample status
- **AND** the UI explains that the output sample is too short

#### Scenario: Historical estimate remains distinguishable

- **GIVEN** all three new timing/sample fields are null and otherwise valid historical timing/usage has a window of at least 100 ms
- **WHEN** request speed is displayed
- **THEN** the old TTFT-based formula may produce a value marked `legacy_estimate`
- **AND** this value is excluded from qualified daily TPS medians
- **AND** missing reasoning usage is not silently interpreted as zero


### Requirement: Optional source metrics cannot interrupt valid forwarding

Source usage parsing MUST preserve reported nonnegative integer reasoning tokens and reject boolean or database-integer-out-of-range token values. Missing reasoning details MUST remain unknown. Optional timing parsing MUST reject non-finite, negative, boolean, overflowing and database-integer-out-of-range values, including overflow when adding individually finite timings, without interrupting an otherwise valid upstream response. Streamed usage/metrics MUST parse complete SSE events, combining multiple data lines and preserving framing across chunk-split CRLF boundaries. Forwarded bytes MUST remain unchanged.

#### Scenario: Overflowing timing sum is ignored

- **GIVEN** individually finite source TTFT and generation timing whose sum overflows
- **WHEN** the response is parsed
- **THEN** optional timing remains absent and response forwarding completes normally

#### Scenario: Multi-line usage event split at CRLF

- **GIVEN** a valid SSE usage/metrics event contains several data lines and a network chunk ends between CR and LF
- **WHEN** stream parsing completes
- **THEN** usage, reasoning and timing match the equivalent single-chunk event

#### Scenario: Unrepresentable source token count

- **GIVEN** an upstream reports a token count outside the request-log database integer range
- **WHEN** usage is parsed
- **THEN** that usage is treated as unavailable rather than causing request-log persistence to overflow
- **AND** the existing fail-closed behavior for API-key limits requiring usage is preserved

## MODIFIED Requirements

### Requirement: Dashboard request logs show generation speed

The dashboard request-log table MUST show gateway-observed TTFT and backend-calculated non-reasoning output TPS when eligible. It MUST distinguish estimated, legacy and unavailable samples and explain that HTTP attempt TTFT excludes separately recorded pre-attempt queue time while WebSocket/bridge request-state timing may include waits. Model-source timings supplied by an upstream MUST be described as upstream-reported metrics. Input tokens MUST NOT enter the TPS numerator.

#### Scenario: TPS excludes TTFT and input tokens

- **GIVEN** 1,000 input tokens, 200 output tokens including 40 reasoning tokens, 1,000 ms completion, 200 ms first non-reasoning output and at least two output chunks
- **WHEN** request speed is shown
- **THEN** it displays approximately 200.0 TPS with an estimate explanation

#### Scenario: missing speed inputs stay blank

- **GIVEN** a request lacks required timing or usage evidence
- **WHEN** the dashboard displays its speed
- **THEN** it shows no numeric TPS and explains the unavailable sample status

### Requirement: Reports show daily median generation speed trends

The Reports dashboard MUST expose daily median gateway TTFT, qualified non-reasoning TPS and queue wait. Latency/speed medians with no eligible samples MUST be null and render as missing rather than zero. TPS MUST include only successful normal requests meeting the current sample-quality criteria, exclude prewarm, incomplete and legacy-only samples, and expose the eligible daily TPS sample count. TTFT and queue medians MUST exclude failed/cancelled and warmup/prewarm samples, negative values and invalid timing relationships. The existing report retention policy that includes soft-deleted history MUST remain unchanged.

#### Scenario: No eligible speed samples

- **GIVEN** a report day contains only legacy or insufficient TPS samples
- **WHEN** the report is generated
- **THEN** median TPS is null and the eligible TPS sample count is zero
- **AND** the chart does not render a measured zero speed

#### Scenario: Daily speed charts use median valid request values

- **GIVEN** a day has valid gateway TTFT and qualified TPS samples
- **WHEN** Reports renders that day
- **THEN** it uses the median of eligible per-request values and includes the TPS sample count

#### Scenario: Missing daily speed data remains unknown

- **GIVEN** a selected report day has no eligible speed samples
- **WHEN** Reports fills the day
- **THEN** only its eligible sample count is zero-filled
- **AND** its missing TTFT, TPS and queue-wait medians remain null

#### Scenario: Daily queue-wait trend surfaces load-balancer wait

- **GIVEN** successful normal requests contain valid non-null queue-wait samples
- **WHEN** Reports renders that day
- **THEN** it displays their median and preserves a measured zero queue wait
- **AND** a day without samples has null queue wait

### Requirement: Request speed timings share one anchor and expose queue wait

For a single request-log row, total latency, upstream terminal latency, TTFT and first non-reasoning output latency MUST use the same attempt/request-state anchor. SSE MUST keep account selection, admission and previous failed attempts in nullable `latency_queue_ms` outside that attempt's timings. WebSocket and HTTP bridge MAY retain their request-state anchors and dedicated queue-phase fields. TTFT MUST recognize visible output including reasoning; non-reasoning TPS MUST instead use its separately captured first non-reasoning output timestamp. Hidden model reasoning MUST NOT be reconstructed from usage counts.

#### Scenario: Failover no longer inflates TTFT

- **GIVEN** a request fails over and subsequently completes successfully
- **WHEN** the successful request row is persisted
- **THEN** its completion, TTFT and first non-reasoning output share the successful attempt's anchor
- **AND** no failed-attempt terminal timestamp truncates its duration

#### Scenario: Reasoning delta counts as the first token

- **GIVEN** visible reasoning arrives before text output
- **WHEN** timings are recorded
- **THEN** TTFT uses the reasoning event and first non-reasoning output uses the text event

#### Scenario: Single-anchor rows on websocket and bridge paths

- **WHEN** WebSocket or HTTP bridge records completion, TTFT and first non-reasoning output
- **THEN** they share the same request-state anchor
- **AND** dedicated queue-phase fields may be used instead of `latency_queue_ms`

### Requirement: Websocket responses capture request-log latency timings

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. First-token latency MUST use the first token-bearing output delta, including text, refusal, reasoning-summary, function-call argument, custom-tool input, and tool-call output deltas, or a custom/apply-patch tool-call `response.output_item.added` or `response.output_item.done` event only when the item contains meaningful tool-call payload content and the tool protocol does not stream argument deltas. Nonempty done-only text, refusal and tool argument/input events or a terminal payload containing actual output MUST also establish first-token timing when no earlier visible output was observed. Positive usage alone MUST NOT establish first-token timing. Recording MUST use upstream observation time and MUST NOT change routing, failover, or the bytes returned to the client.

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
- **THEN** first-token latency remains null until a token-bearing output delta arrives, unless a meaningful content-bearing completion event anchors TTFT for a completion-only protocol
- **AND** a message, reasoning, or function-call `response.output_item.added` lifecycle event does not record first-token latency
- **AND** reasoning-summary placeholder deltas that are stripped before delivery do not record first-token latency
- **AND** metadata-only or empty tool-call delta and completion events do not record first-token latency
