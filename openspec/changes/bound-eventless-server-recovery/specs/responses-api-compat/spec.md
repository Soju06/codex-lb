# responses-api-compat Delta

## ADDED Requirements

### Requirement: Eventless server-owned bridge recovery is bounded

The proxy MUST retry server-owned HTTP bridge recovery only up to the
configured `_HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS` budget after
consecutive eligible eventless failures for an anchored continuation. Once
that budget is exhausted, the proxy MUST stop recovering and emit a terminal
`response.failed` event.

That terminal event MUST include a stable `response.id` even when upstream
never emitted `response.created` or another response envelope before the
failure. Public `/v1/responses` normalization depends on that envelope to
synthesize the required leading `response.created` event without producing an
SDK parser failure.

#### Scenario: Exhausted eventless recovery terminates with one response id

- **GIVEN** an anchored HTTP bridge continuation is eligible for server-owned
  recovery
- **AND** each upstream attempt fails before any downstream `response.*` event
- **WHEN** the bridge reaches its configured eventless recovery attempt cap
- **THEN** it emits one terminal `response.failed` event instead of continuing
  recovery indefinitely
- **AND** that terminal event includes a stable `response.id`
