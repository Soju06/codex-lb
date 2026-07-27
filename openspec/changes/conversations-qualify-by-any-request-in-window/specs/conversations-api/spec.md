## ADDED Requirements

### Requirement: Conversations qualify by any in-window request

The `/api/conversations` listing endpoint SHALL treat a conversation as belonging to a `since` time window when at least one of its `request_logs` rows has `requested_at >= since`. A conversation that started before the window but has any request inside the window MUST be included. A conversation whose every row predates `since` MUST be excluded. The endpoint MUST NOT use the conversation's earliest request timestamp as a membership gate.

#### Scenario: Long-running conversation appears when active in window
- **GIVEN** conversation `conv-a` has one `request_logs` row at `T - 60 days` and one row at `T - 1 day`
- **WHEN** the operator requests `GET /api/conversations?since=T - 30 days`
- **THEN** the response includes `conv-a`

#### Scenario: Conversation with no in-window rows is excluded
- **GIVEN** conversation `conv-b` has `request_logs` rows only at `T - 60 days` and `T - 45 days`
- **WHEN** the operator requests `GET /api/conversations?since=T - 30 days`
- **THEN** the response excludes `conv-b`

#### Scenario: Conversation starting inside the window is still included
- **GIVEN** conversation `conv-c` has its first and all subsequent rows after `since`
- **WHEN** the operator requests `GET /api/conversations?since=T - 30 days`
- **THEN** the response includes `conv-c`

### Requirement: Conversation start timestamp is the true earliest request

The `/api/conversations` list and `/api/conversations/{id}` detail responses SHALL report `firstRequest` (list) and `start` (detail) as the minimum `requested_at` over all eligible `request_logs` rows for that `conversation_id`, regardless of the `since` window. The reported start timestamp MAY fall before `since` when a long-running conversation is surfaced in a recent window. The API MUST NOT clamp the start timestamp to the window boundary and MUST NOT introduce a window-relative start field.

#### Scenario: Surfaced conversation reports a pre-window start
- **GIVEN** conversation `conv-a` has its earliest row at `T - 60 days` and a later row at `T - 1 day`
- **WHEN** the operator requests `GET /api/conversations?since=T - 30 days`
- **THEN** the `conv-a` entry's `firstRequest` field equals the `T - 60 days` timestamp

#### Scenario: Conversation with only in-window rows reports its earliest in-window row
- **GIVEN** conversation `conv-c` has its earliest row at `T - 5 days`, entirely inside the window
- **WHEN** the operator requests `GET /api/conversations?since=T - 30 days`
- **THEN** the `conv-c` entry's `firstRequest` field equals the `T - 5 days` timestamp

### Requirement: Conversation list window is bounded by a 30-day lookback

When `/api/conversations` is requested without an explicit `since`, the endpoint SHALL apply an effective `since` of `utcnow() - 30 days`. The endpoint MUST reject or clamp any caller-supplied `since` older than 30 days against the same cap. The cap bounds activity lookback; it does not require a conversation to have started within the window.

#### Scenario: Bare request defaults to the last 30 days of activity
- **GIVEN** conversations exist with activity in the last 30 days and conversations with activity only older than 30 days
- **WHEN** the operator requests `GET /api/conversations` with no `since`
- **THEN** only conversations with at least one row in the last 30 days are returned

#### Scenario: Caller since older than 30 days is bounded
- **GIVEN** the operator supplies `since=T - 90 days`
- **WHEN** the request is processed
- **THEN** the effective window is clamped to `utcnow() - 30 days`

### Requirement: Conversation list membership agrees with dashboard activity aggregations

The membership rule used by `/api/conversations` (any in-window request qualifies) SHALL match the rule used by the dashboard activity and trends aggregations that count distinct conversations by `requested_at` window. A conversation that appears in the `/api/conversations` list for a window MUST also be counted by the dashboard activity aggregation for the same window, and vice versa. This requirement exists to resolve a pre-existing inconsistency between the two views.

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
