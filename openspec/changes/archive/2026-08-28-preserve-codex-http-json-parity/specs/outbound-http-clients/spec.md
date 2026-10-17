## ADDED Requirements

### Requirement: Client-to-LB routing hints remain hop-local

The service MUST treat `x-codex-routing-hint` as client-to-LB metadata. The
header MAY be inspected by local routing or test harnesses, but it MUST NOT be
included in any upstream HTTP request or WebSocket handshake. Header matching
MUST be case-insensitive.

#### Scenario: HTTP egress omits routing hint

- **GIVEN** an inbound request includes `x-codex-routing-hint`
- **WHEN** codex-lb builds an upstream Responses HTTP request
- **THEN** the upstream request MUST NOT include that header.

#### Scenario: WebSocket egress omits routing hint

- **GIVEN** an inbound WebSocket handshake includes any case spelling of
  `x-codex-routing-hint`
- **WHEN** codex-lb builds an upstream Responses WebSocket handshake
- **THEN** the upstream handshake MUST NOT include that header.
