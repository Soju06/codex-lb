# Responses API compatibility delta

## ADDED Requirements

### Requirement: classify edge challenge 403 narrowly

The proxy MUST classify a WebSocket handshake as an edge challenge only when
the response status is 403 and the response carries explicit challenge
evidence. Structured JSON permission errors, local `ip_forbidden`, ordinary
Nginx HTML, and missing evidence MUST remain non-challenge failures.

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

### Requirement: recover edge challenges in automatic transport mode

When automatic upstream transport is selected and a pre-dispatch edge
challenge 403 is received, the proxy MUST attempt at most one same-account
HTTP Responses request. The retry MUST NOT increment account error backoff or
switch a hard-pinned account.

#### Scenario: automatic transport falls back once

- **WHEN** the upstream WebSocket handshake returns an edge challenge before
  any response event
- **AND** the request is movable and has no unsettled reservation
- **THEN** the proxy retries the request once over HTTP on the same account

#### Scenario: forced WebSocket preserves the challenge error

- **WHEN** upstream transport is forced to WebSocket
- **AND** the handshake returns an edge challenge
- **THEN** the proxy does not retry over HTTP

### Requirement: preserve replay and security boundaries

Requests with visible upstream output, previous-response/file ownership, or an
unsettled API-key reservation MUST NOT be replayed over HTTP. The change MUST
NOT relax API firewall enforcement, forwarded-header trust, API-key scope
checks, or structured upstream permission errors.

#### Scenario: submitted request is not replayed

- **WHEN** an edge challenge-like failure is observed after upstream output
- **THEN** the proxy surfaces the failure without a second dispatch

#### Scenario: local firewall denial remains forbidden

- **WHEN** the API firewall rejects a client IP
- **THEN** the proxy returns `403 ip_forbidden` and does not invoke transport
  fallback

### Requirement: concurrent cleanup treats downstream disconnect as cancellation

When a downstream WebSocket disconnects while the proxy is sending a
keepalive or terminal recovery event, the proxy MUST mark downstream activity
as disconnected and continue cleanup without an uncaught ASGI error. Other
send failures MUST remain visible.

#### Scenario: disconnected client does not create an ASGI error

- **WHEN** a client closes its WebSocket while waiting for account capacity
- **THEN** keepalive delivery stops, the request is recorded as client
  cancellation, and the connection lease is released

### Requirement: Docker validation is isolated

The validation procedure MUST use a dedicated Docker deployment and temporary
Codex homes, MUST run 16 concurrent workers for three rounds, and MUST verify
that host Codex configuration files are unchanged.

#### Scenario: concurrent Docker Codex validation

- **WHEN** 16 isolated Codex workers run for three rounds through the proxy
- **THEN** client-visible forbidden errors, unbounded reconnect loops, and
  account-health poisoning are absent under a healthy upstream
