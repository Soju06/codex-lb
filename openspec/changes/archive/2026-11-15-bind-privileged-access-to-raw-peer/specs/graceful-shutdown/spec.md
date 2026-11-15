## ADDED Requirements

### Requirement: Internal drain control requires captured loopback transport provenance

Internal drain start, stop and status routes MUST authorize using the launcher-captured raw socket peer under their existing loopback-only classification. A forwarded loopback client projection MUST NOT authorize a non-loopback raw peer. An unavailable raw peer MUST return HTTP 403. Rejection MUST precede deadline parsing, shutdown-state mutation and bridge drain/status callbacks. Existing authorized drain behavior, deadlines and response schemas MUST remain unchanged.

#### Scenario: A remote peer projects loopback

- **GIVEN** a captured IPv4 or IPv6 raw peer is not loopback
- **WHEN** it requests any internal drain route with a loopback forwarded client projection
- **THEN** the route returns HTTP 403 without changing shutdown state or invoking bridge callbacks
- **AND** drain start returns HTTP 403 even when the supplied deadline is malformed

#### Scenario: A loopback peer projects a public client

- **WHEN** a captured loopback raw peer requests drain start, status or stop with a public forwarded client projection
- **THEN** the route retains its existing authorized behavior and response contract

#### Scenario: A loopback projection has no captured provenance

- **WHEN** the captured raw peer is unavailable for any internal drain route
- **THEN** the route returns HTTP 403 even when the projected client is loopback
