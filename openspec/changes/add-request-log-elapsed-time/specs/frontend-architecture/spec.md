## ADDED Requirements

### Requirement: Dashboard request-log details compare elapsed and latency timings
The dashboard request-log API response MUST expose the persisted request-log `elapsedMs` value when present. The Request Details dialog MUST render an `Elapsed Time` field beside `Plan` using the format `elapsed (latency)`, where `elapsed` uses `elapsedMs`, `latency` uses `latencyMs`, and the parenthesized latency text is visually de-emphasized with smaller dimmer styling.

#### Scenario: Request details show elapsed and latency together
- **WHEN** a request log entry is stored with `elapsedMs: 1243` and `latencyMs: 1876`
- **THEN** the `GET /api/request-logs` response includes both values for that row
- **AND** the Request Details dialog shows `Elapsed Time` as `1.2 s (1.9 s)`

#### Scenario: Duration formatting keeps milliseconds up to one second
- **WHEN** a request log entry is stored with `elapsedMs: 1000` and `latencyMs: 999.94`
- **THEN** the Request Details dialog shows `Elapsed Time` as `1000.0 ms (999.9 ms)`

#### Scenario: Legacy request log row without elapsed timing still renders
- **WHEN** a request log entry has `elapsedMs: null` and `latencyMs: 842`
- **THEN** the `GET /api/request-logs` response includes `elapsedMs: null` or omits it as a nullable field
- **AND** the Request Details dialog still renders without failing
