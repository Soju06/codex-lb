## MODIFIED Requirements

### Requirement: Expensive upstream work is admission controlled

The proxy MUST enforce separate in-process admission limits for token refresh, upstream websocket connect, and first-turn response creation.

#### Scenario: Owner-switch blocked websocket releases response-create admission

- **GIVEN** a websocket request has acquired response-create admission
- **AND** the request cannot switch to its required previous-response owner because another request is still streaming on the current upstream socket
- **WHEN** the proxy emits `previous_response_owner_unavailable` for the blocked request
- **THEN** it releases that request's response-create gate and account response-create lease
- **AND** later eligible requests are not blocked by stale local response-create pressure
