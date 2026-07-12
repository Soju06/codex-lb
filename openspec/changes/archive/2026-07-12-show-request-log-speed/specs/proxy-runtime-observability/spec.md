## ADDED Requirements

### Requirement: Dashboard request logs show generation speed

The dashboard request-log table MUST show time to first token and output-token generation speed when the required latency and output-token fields are available. Generation speed MUST use output tokens divided by elapsed generation time after time to first token, not total input plus output tokens and not total request latency including TTFT.

#### Scenario: TPS excludes TTFT and input tokens

- **GIVEN** a successful request log has 1,000 input tokens, 200 output tokens, 1,000 ms total latency, and 200 ms TTFT
- **WHEN** the dashboard renders request logs
- **THEN** it shows TTFT as 200ms
- **AND** it shows TPS as 250.0

#### Scenario: missing speed inputs stay blank

- **GIVEN** a request log is missing TTFT, total latency, or output tokens
- **WHEN** the dashboard renders request logs
- **THEN** it does not show a misleading calculated TPS value

### Requirement: Reports show daily median generation speed trends

The Reports dashboard MUST expose daily median TTFT and daily median TPS trends when request-log latency fields are available. Empty days and rows with no valid timing/speed inputs MUST render as zero in those trend charts. Daily TPS MUST median per-request output-token TPS after TTFT rather than use input tokens or include TTFT wait time.

#### Scenario: Daily speed charts use median valid request values

- **GIVEN** one report day has request logs with TTFT and output-token TPS values
- **WHEN** the dashboard renders Reports
- **THEN** it shows a Time to First Token chart using median TTFT for the day
- **AND** it shows a Tokens per Second chart using median per-request TPS for the day

#### Scenario: Missing daily speed data is zero-filled

- **GIVEN** a selected report range includes a day with no request logs or no valid timing data
- **WHEN** the dashboard renders Reports
- **THEN** the TTFT and TPS charts include that day with value zero

### Requirement: Websocket responses capture request-log latency timings

The websocket responses proxy path MUST record first-upstream-event, response-created, and first-token latency into the same request-log latency fields the HTTP bridge populates, so websocket request logs expose TTFT and generation speed. Recording MUST NOT change routing, failover, or the bytes returned to the client.

#### Scenario: Websocket request log records latency timings

- **GIVEN** a websocket responses request whose upstream emits a `response.created` event, then a text delta, then completion
- **WHEN** the proxy persists the request log
- **THEN** the log has non-null first-upstream-event, response-created, and first-token latency values
- **AND** first-upstream-event latency is less than or equal to response-created latency, which is less than or equal to first-token latency
