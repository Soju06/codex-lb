## ADDED Requirements

### Requirement: Request kind and session correlation are visible in the dashboard
The Dashboard recent requests table SHALL render each row's recorded `requestKind` so operators can distinguish standard Responses traffic from compact traffic without leaving the UI. When `/api/request-logs` includes a `sessionIdHash`, the same row SHALL show that hashed value as a safe correlation key. The table SHALL remain renderable for legacy rows whose `requestKind` or `sessionIdHash` is missing.

#### Scenario: Compact request row is visible in the dashboard
- **WHEN** `/api/request-logs` returns a request row with `requestKind = "compact"` and `sessionIdHash = "sha256:abc123def456"`
- **THEN** the recent requests table shows a visible compact request indicator for that row
- **AND** the row shows the hashed session correlation value without exposing the raw `session_id`

#### Scenario: Legacy request row without kind or session hash still renders
- **WHEN** `/api/request-logs` returns a request row with `requestKind = null` and `sessionIdHash = null`
- **THEN** the recent requests table still renders the row
- **AND** it shows neutral placeholders instead of breaking layout

### Requirement: Request kind and transport are filterable in the dashboard
The Dashboard request-log filters SHALL expose `requestKind` and `transport` as selectable facets so operators can narrow recent request rows by the same route and transport signals shown in the table. The request-log filter-options API response SHALL include distinct `requestKinds` and `transports` values for the current non-status filter scope.

#### Scenario: Request kind and transport facets narrow the request-log query
- **WHEN** the user selects `requestKind = "compact"` and `transport = "websocket"` in the dashboard filters
- **THEN** the frontend refetches `GET /api/request-logs` with `requestKind=compact` and `transport=websocket`
- **AND** the visible recent requests table only shows rows matching both selections

#### Scenario: Filter options expose request kind and transport facets
- **WHEN** the frontend fetches `GET /api/request-logs/options` for the current non-status filter scope
- **THEN** the response includes `requestKinds` and `transports` alongside the existing account, model, and status options
