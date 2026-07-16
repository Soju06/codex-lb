# proxy-runtime-observability delta

## ADDED Requirements

### Requirement: Request speed timings share one anchor and expose queue wait

For a single request-log row, `latency_ms` and `latency_first_token_ms` MUST be
measured from the same anchor: the start of the attempt that produced the row.
Time spent before that attempt — account selection, admission waits, and failed
failover attempts — MUST NOT inflate `latency_first_token_ms`; the HTTP
streaming path MUST record it instead in a nullable `latency_queue_ms`
request-log column. First-token detection MUST treat the first output delta of
any kind — visible text, refusal, or reasoning deltas — as the first token, so
TTFT means time to first model output and the generation window
(`latency_ms - latency_first_token_ms`) covers reasoning generation, matching
the reasoning-inclusive `output_tokens` numerator used for TPS.

#### Scenario: Failover no longer inflates TTFT

- **GIVEN** a streaming request fails over from one account and succeeds on the
  next attempt
- **WHEN** the request log is persisted
- **THEN** `latency_first_token_ms` reflects only the successful attempt
- **AND** `latency_queue_ms` records the pre-attempt time (selection plus the
  failed attempt)
- **AND** `latency_ms` is greater than or equal to `latency_first_token_ms`

#### Scenario: Reasoning delta counts as the first token

- **GIVEN** an upstream stream emits a reasoning summary delta before the first
  visible text delta
- **WHEN** first-token latency is captured
- **THEN** `latency_first_token_ms` anchors to the reasoning delta rather than
  waiting for visible text

#### Scenario: Single-anchor rows on websocket and bridge paths

- **WHEN** a websocket or HTTP bridge request records latency timings
- **THEN** `latency_ms` and `latency_first_token_ms` derive from the same
  request-state anchor
- **AND** `latency_queue_ms` MAY be null on paths whose queue waits are already
  recorded in dedicated phase columns

## MODIFIED Requirements

### Requirement: Reports show daily median generation speed trends

The Reports dashboard MUST expose daily median TTFT, daily median TPS, and daily median queue-wait trends when request-log latency fields are available. Empty days and rows with no valid timing/speed inputs MUST render as zero in those trend charts. Daily TPS MUST median per-request output-token TPS after TTFT rather than use input tokens or include TTFT wait time. Daily queue wait MUST median per-request `latency_queue_ms` over rows where it is non-null.

#### Scenario: Daily speed charts use median valid request values

- **GIVEN** one report day has request logs with TTFT and output-token TPS values
- **WHEN** the dashboard renders Reports
- **THEN** it shows a Time to First Token chart using median TTFT for the day
- **AND** it shows a Tokens per Second chart using median per-request TPS for the day

#### Scenario: Missing daily speed data is zero-filled

- **GIVEN** a selected report range includes a day with no request logs or no valid timing data
- **WHEN** the dashboard renders Reports
- **THEN** the TTFT and TPS charts include that day with value zero

#### Scenario: Daily queue-wait trend surfaces load-balancer wait

- **GIVEN** a report day has request logs with non-null `latency_queue_ms`
- **WHEN** the dashboard renders Reports
- **THEN** it shows a queue-wait trend using the day's median `latency_queue_ms`
- **AND** days without queue samples render as zero
