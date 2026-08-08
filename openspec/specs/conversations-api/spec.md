# Conversations API

## Purpose

Normative contract for the `/api/conversations` listing and `/api/conversations/{id}` detail endpoints. Conversations are a **derived view** over `request_logs` grouped by `conversation_id` — there is no stored conversation entity. This capability pins the membership, windowing, and timestamp semantics for that derived view.

## Requirements

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

### Requirement: Conversation membership candidate discovery is bounded

When the conversations list has an effective `since` value, the repository MUST discover qualifying conversation IDs from eligible rows with
`requested_at >= since` before aggregating the list page. The candidate query
MUST select distinct IDs and MUST include the active search predicate when a
search term is supplied. The summary and facet aggregates MUST constrain their
full-history eligible-row scans to those candidate IDs; they MUST NOT use an
unbounded grouped summary with a `HAVING` activity filter as the membership
gate.

The full-history aggregate semantics remain unchanged for each selected ID:
`firstRequest`, request counts, token totals, costs, and facets MUST include
all eligible rows for that conversation, including rows older than `since`.

#### Scenario: Inactive history is pruned before summary aggregation
- **GIVEN** `conv-a` has eligible rows before and after `since`
- **AND** `conv-b` has eligible rows only before `since`
- **WHEN** the operator requests the conversation list for that `since`
- **THEN** candidate discovery includes `conv-a` and excludes `conv-b` before the grouped summary
- **AND** `conv-a`'s summary still includes its pre-window rows

#### Scenario: Search candidate discovery uses the activity window
- **GIVEN** an eligible active conversation has a matching search value on a row at or after `since`
- **WHEN** the operator searches the conversation list for that value
- **THEN** the distinct search candidate query is constrained by `requested_at >= since`
- **AND** the returned conversation's aggregate still includes all of its eligible history

### Requirement: Conversation list and detail routes require an admin principal

The `/api/conversations` collection aliases and `/api/conversations/{id}` detail route MUST require an `admin` dashboard
principal before reading conversation data. A non-admin principal MUST receive
HTTP 403 with error code `admin_access_required`; the route MUST NOT return a
conversation payload. Admin requests SHALL retain all existing membership,
windowing, timestamp, aggregation, pagination, and response-schema behavior.

#### Scenario: Guest collection access is denied

- **WHEN** a guest principal requests `/api/conversations` or `/api/conversations/`
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no conversation list is returned

#### Scenario: Guest detail access is denied

- **WHEN** a guest principal requests `/api/conversations/{id}`
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no conversation detail is returned

#### Scenario: Admin conversation access is unchanged

- **WHEN** an admin principal requests a collection or detail route
- **THEN** the existing conversation response is returned
