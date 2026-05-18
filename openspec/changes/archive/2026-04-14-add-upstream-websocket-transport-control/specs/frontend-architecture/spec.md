## ADDED Requirements
### Requirement: Routing settings expose upstream streaming transport
The Settings page routing section SHALL expose an upstream streaming transport control with `default`, `auto`, `http`, and `websocket` options, and saving the control SHALL persist through `PUT /api/settings`.

#### Scenario: Save websocket upstream transport
- **WHEN** a user selects `websocket` in the routing settings section and saves
- **THEN** the app calls `PUT /api/settings` with `upstreamStreamTransport: "websocket"`
- **AND** the next settings read reflects `upstreamStreamTransport: "websocket"`

#### Scenario: Save default upstream transport
- **WHEN** a user selects `default` in the routing settings section and saves
- **THEN** the app calls `PUT /api/settings` with `upstreamStreamTransport: "default"`
- **AND** server-side routing falls back to the configured environment default
