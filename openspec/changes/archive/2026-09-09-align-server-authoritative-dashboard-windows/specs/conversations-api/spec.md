## ADDED Requirements

### Requirement: Conversations endpoint accepts a server-authoritative timeframe parameter

The `/api/conversations` listing endpoint SHALL accept an optional `timeframe` query parameter with values `1d`, `7d`, or `30d`. When `timeframe` is supplied, the endpoint SHALL derive the effective `since` window from the server's own UTC clock using the same timeframe configuration (window duration in minutes) that the dashboard overview activity aggregation uses, so that a row counted by the dashboard activity aggregation for that timeframe is always listed by the conversations endpoint for the same timeframe under a fixed server clock, and vice versa. The endpoint MUST NOT accept a client-supplied timestamp as a substitute for the server-derived window when `timeframe` is supplied. The client MAY still send the legacy `since` parameter as a compatibility escape hatch; see the mutual-exclusion requirement below.

#### Scenario: Timeframe parameter derives the window from the server clock

- **GIVEN** the server clock is fixed at `T` and the dashboard overview timeframe config maps `7d` to `10080` minutes
- **WHEN** the operator requests `GET /api/conversations?timeframe=7d`
- **THEN** the effective window start is exactly `T - 10080 minutes` as computed by the server
- **AND** the response membership is identical to the dashboard activity aggregation for `timeframe=7d` under the same fixed server clock

#### Scenario: Timeframe matches dashboard overview membership under a fixed clock

- **GIVEN** conversation `conv-a` has at least one `request_logs` row inside the server-derived 7-day window and `conv-b` has rows only outside it
- **WHEN** the dashboard activity aggregation for `timeframe=7d` counts `conv-a` and excludes `conv-b` under a fixed server clock
- **AND** the operator requests `GET /api/conversations?timeframe=7d` against the same fixed server clock
- **THEN** the conversations list includes `conv-a` and excludes `conv-b`

#### Scenario: Browser clock skew does not affect the conversations window

- **GIVEN** the browser clock is skewed days ahead of the server clock
- **WHEN** the dashboard client requests `GET /api/conversations?timeframe=7d`
- **THEN** the request contains only the `timeframe` parameter and no browser-generated `since`
- **AND** the server-derived window is unaffected by the browser clock skew

#### Scenario: Each documented timeframe key is accepted

- **WHEN** the operator requests `GET /api/conversations?timeframe=1d`
- **THEN** the server derives the window from the `1d` config entry
- **WHEN** the operator requests `GET /api/conversations?timeframe=7d`
- **THEN** the server derives the window from the `7d` config entry
- **WHEN** the operator requests `GET /api/conversations?timeframe=30d`
- **THEN** the server derives the window from the `30d` config entry

#### Scenario: Unknown timeframe key is rejected

- **WHEN** the operator requests `GET /api/conversations?timeframe=24h`
- **THEN** the endpoint rejects the request with a validation error and does not fall back to any default window

### Requirement: Timeframe and since are mutually exclusive on the conversations endpoint

The `/api/conversations` endpoint SHALL reject any request that supplies both `timeframe` and `since` rather than silently choosing precedence between them. The endpoint SHALL accept a request that supplies exactly one of the two parameters, and SHALL apply the 30-day lookback cap and timezone normalization to a standalone `since` exactly as it did before this change. The endpoint SHALL continue to accept a bare request (neither parameter) and apply the default 30-day window.

#### Scenario: Both timeframe and since are rejected

- **WHEN** the operator requests `GET /api/conversations?timeframe=7d&since=2025-01-01T00:00:00Z`
- **THEN** the endpoint rejects the request with a validation error
- **AND** the endpoint does not compute or apply any window

#### Scenario: Legacy since continues to work without timeframe

- **GIVEN** the operator supplies `since=T - 5 days` with no `timeframe` parameter
- **WHEN** the request is processed
- **THEN** the endpoint applies the supplied `since` with the existing UTC normalization and 30-day cap behavior

#### Scenario: Bare request continues to default to the 30-day window

- **WHEN** the operator requests `GET /api/conversations` with neither `timeframe` nor `since`
- **THEN** the effective window is `utcnow() - 30 days`, matching the pre-existing default

## MODIFIED Requirements

### Requirement: Conversation list window is bounded by a 30-day lookback

When `/api/conversations` is requested without an explicit window parameter, the endpoint SHALL apply an effective `since` of `utcnow() - 30 days`. The endpoint MUST reject or clamp any caller-supplied `since` older than 30 days against the same cap. The cap bounds activity lookback; it does not require a conversation to have started within the window. When the `timeframe` parameter is supplied, the endpoint SHALL derive `since` from the shared dashboard timeframe configuration instead of the caller's clock; the `30d` timeframe key produces the same window as the bare default, and the `1d` and `7d` keys produce shorter windows from the same configuration table.

#### Scenario: Bare request defaults to the last 30 days of activity

- **GIVEN** conversations exist with activity in the last 30 days and conversations with activity only older than 30 days
- **WHEN** the operator requests `GET /api/conversations` with no `since` and no `timeframe`
- **THEN** only conversations with at least one row in the last 30 days are returned

#### Scenario: Caller since older than 30 days is bounded

- **GIVEN** the operator supplies `since=T - 90 days` with no `timeframe`
- **WHEN** the request is processed
- **THEN** the effective window is clamped to `utcnow() - 30 days`

#### Scenario: Timeframe 30d produces the bare-default window

- **GIVEN** the server clock is fixed at `T`
- **WHEN** the operator requests `GET /api/conversations?timeframe=30d`
- **THEN** the effective window is `T - 30 days`, matching the bare-request default under the same clock

#### Scenario: Timeframe shorter than 30 days produces a narrower window

- **GIVEN** the server clock is fixed at `T`
- **WHEN** the operator requests `GET /api/conversations?timeframe=7d`
- **THEN** the effective window is `T - 7 days`, narrower than the 30-day default

### Requirement: Conversation list membership agrees with dashboard activity aggregations

The membership rule used by `/api/conversations` (any in-window request qualifies) SHALL match the rule used by the dashboard activity and trends aggregations that count distinct conversations by `requested_at` window. A conversation that appears in the `/api/conversations` list for a window MUST also be counted by the dashboard activity aggregation for the same window, and vice versa. This requirement exists to resolve a pre-existing inconsistency between the two views. When the conversations endpoint is invoked with the `timeframe` parameter, the window SHALL be derived from the same shared timeframe configuration as the dashboard overview activity aggregation so that the two views agree under a single server clock rather than depending on the client clock.

#### Scenario: Conversation counted by dashboard trends is listed by the conversations endpoint

- **GIVEN** conversation `conv-a` has rows both before and inside the 7-day dashboard window
- **WHEN** the dashboard activity aggregation for the window counts `conv-a`
- **AND** the operator requests `GET /api/conversations?since=<window start>`
- **THEN** the conversations list also includes `conv-a`

#### Scenario: Conversation excluded by dashboard trends is excluded by the conversations endpoint

- **GIVEN** conversation `conv-b` has rows only outside the window
- **WHEN** the dashboard activity aggregation for the window does not count `conv-b`
- **AND** the operator requests `GET /api/conversations?since=<window start>`
- **THEN** the conversations list also excludes `conv-b`

#### Scenario: Timeframe mode agrees with dashboard overview under the same server clock

- **GIVEN** conversation `conv-a` has a row inside the server-derived 7-day window and `conv-b` has rows only outside it
- **WHEN** the dashboard activity aggregation for `timeframe=7d` counts `conv-a` and excludes `conv-b` under a fixed server clock
- **AND** the operator requests `GET /api/conversations?timeframe=7d` against the same fixed server clock
- **THEN** the conversations list includes `conv-a` and excludes `conv-b`, agreeing with the dashboard aggregation row-for-row
