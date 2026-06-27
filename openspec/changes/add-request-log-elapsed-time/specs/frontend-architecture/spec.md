## ADDED Requirements

### Requirement: Dashboard request-log details compare elapsed and latency timings
The dashboard request-log API response MUST expose the persisted request-log `elapsedMs` value when present. The Request Details dialog MUST render an `Upstream elapsed` field that uses `elapsedMs` and a separate `Total elapsed` field that uses `latencyMs`. Both fields MUST use the same duration formatter so operators can compare upstream duration against the broader proxy duration directly.

#### Scenario: Request details show elapsed and latency together
- **WHEN** a request log entry is stored with `elapsedMs: 1243` and `latencyMs: 1876`
- **THEN** the `GET /api/request-logs` response includes both values for that row
- **AND** the Request Details dialog shows `Upstream elapsed` as `1.2 s`
- **AND** the Request Details dialog shows `Total elapsed` as `1.9 s`

#### Scenario: Duration formatting keeps milliseconds up to one second
- **WHEN** a request log entry is stored with `elapsedMs: 1000` and `latencyMs: 999.94`
- **THEN** the Request Details dialog shows `Upstream elapsed` as `1000.0 ms`
- **AND** the Request Details dialog shows `Total elapsed` as `999.9 ms`

#### Scenario: Legacy request log row without elapsed timing still renders
- **WHEN** a request log entry has `elapsedMs: null` and `latencyMs: 842`
- **THEN** the `GET /api/request-logs` response includes `elapsedMs: null` or omits it as a nullable field
- **AND** the Request Details dialog shows `Upstream elapsed` as `—`
- **AND** the Request Details dialog shows `Total elapsed` as `842.0 ms`
