## ADDED Requirements

### Requirement: Pre-commit ambiguous websocket 403 failures fail over by account

When a Responses-serving path has not yet emitted downstream bytes, events, or frames, the service MUST treat an ambiguous upstream websocket connect `403` as an account-scoped failure rather than immediately surfacing it to the client. The retry MUST stay on the same upstream transport family; the service MUST NOT silently downgrade to HTTP only because a websocket connect attempt returned `403`.

#### Scenario: Websocket connect 403 fails over to another eligible account

- **WHEN** the proxy selects an account for a Responses request that requires an upstream websocket connect
- **AND** the upstream websocket connect fails with HTTP `403`
- **AND** the failure does not carry a permanent deactivation signal
- **AND** the proxy has not yet emitted any downstream bytes, events, or frames
- **AND** another eligible account exists
- **THEN** the proxy places the failed account into a temporary runtime cooldown for websocket-connect selection
- **AND** it retries the same request against another eligible account on the same transport family
- **AND** it does not silently downgrade to HTTP only because of that `403`

#### Scenario: Final ambiguous websocket connect 403 remains visible

- **WHEN** a pre-commit upstream websocket connect fails with HTTP `403`
- **AND** no other eligible account can serve the request
- **THEN** the proxy surfaces the final upstream `403` to the client
- **AND** the proxy records the failure as a client-exposed pre-commit upstream error
