## ADDED Requirements

### Requirement: Request log transport is visible in the dashboard
The Dashboard recent requests table SHALL display each row's recorded request transport so operators can distinguish websocket and HTTP proxy traffic without leaving the UI. The table SHALL remain renderable for legacy rows whose transport is missing.

#### Scenario: Websocket request row is visible
- **WHEN** `/api/request-logs` returns a request row with `transport = "websocket"`
- **THEN** the recent requests table shows a visible websocket transport indicator for that row

#### Scenario: Legacy request row without transport still renders
- **WHEN** `/api/request-logs` returns a request row with `transport = null`
- **THEN** the recent requests table still renders the row and shows a neutral placeholder instead of breaking layout
