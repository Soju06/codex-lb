# Responses API compatibility delta

## ADDED Requirements

### Requirement: classify edge challenge 403 narrowly

The proxy MUST classify a WebSocket handshake as an edge challenge only when
the response status is 403 and the response carries explicit challenge
evidence (`cf-mitigated: challenge`, or a Cloudflare-identified HTML body
containing known challenge markers). Structured JSON permission errors, local
`ip_forbidden`, ordinary reverse-proxy HTML, and missing evidence MUST remain
non-challenge failures.

#### Scenario: Cloudflare challenge is recognized

- **WHEN** a WebSocket handshake returns 403 with `cf-mitigated: challenge`
- **THEN** the response is classified as an edge challenge

#### Scenario: ordinary permission denial is not a challenge

- **WHEN** a WebSocket handshake returns structured JSON
  `permission_error` with status 403
- **THEN** the response remains a non-challenge permission failure

#### Scenario: unmarked reverse-proxy HTML is not a challenge

- **WHEN** a WebSocket handshake returns an HTML 403 from Nginx without
  challenge evidence
- **THEN** the response remains a non-challenge upstream failure

### Requirement: classified edge challenges are websocket transport failures

A direct-connect WebSocket handshake rejected with a classified edge
challenge MUST carry the same transport-failure provenance as a connect
timeout or 5xx upgrade rejection: the failure MUST surface without recording
an account-health penalty and MUST arm the bounded handshake-denial marker so
Codex clients are steered to the HTTP transport. Routed-proxy handshake
challenges MUST NOT arm the instance-wide marker.

#### Scenario: direct edge challenge steers clients to HTTP

- **WHEN** a direct upstream WebSocket handshake returns a classified edge
  challenge before any response event
- **THEN** the failure surfaces without an account penalty
- **AND** the next Responses WebSocket handshake is denied with HTTP 426
  while the marker is armed

#### Scenario: automatic transport falls back in-request

- **WHEN** the raw streaming path selects the websocket transport in `auto`
  mode
- **AND** the upstream handshake is rejected with a classified edge challenge
- **THEN** the proxy retries the request once over HTTP on the same account

#### Scenario: forced WebSocket preserves the challenge error

- **WHEN** upstream transport is forced to WebSocket
- **AND** the handshake returns an edge challenge
- **THEN** the proxy does not retry over HTTP
