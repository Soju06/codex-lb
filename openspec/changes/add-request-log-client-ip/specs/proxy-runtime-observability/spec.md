## ADDED Requirements

### Requirement: Request logs persist client IP for Responses traffic

The proxy MUST persist the resolved edge client IP on `request_logs.client_ip` for HTTP, SSE, and WebSocket Responses request-log rows when a client IP is available. The proxy MUST resolve the value using the existing trusted-proxy policy, including configured trusted proxy CIDRs and supported forwarded-client-IP headers. When no client IP is available, the persisted value MUST be `null`.

#### Scenario: Direct Responses request stores socket client IP

- **WHEN** a Responses request reaches the proxy without trusted forwarded-client-IP headers
- **THEN** the persisted `request_logs` row stores the socket client IP in `client_ip`

#### Scenario: Trusted proxy request stores forwarded client IP

- **WHEN** a Responses request reaches the proxy from a trusted proxy source with a valid forwarded-client-IP header
- **THEN** the persisted `request_logs` row stores the resolved forwarded client IP in `client_ip`

#### Scenario: HTTP bridge owner logs original client IP

- **WHEN** an origin instance forwards a Responses request to an HTTP bridge owner instance
- **THEN** the owner-side request log stores the client IP resolved by the origin instance

#### Scenario: HTTP bridge replay archives under the retried request

- **WHEN** an HTTP bridge request is retried with a new request-log row and archive id
- **AND** the ambient request id still references the old session request
- **THEN** the upstream request payload is archived under the retried request's archive id

### Requirement: Request-log search matches client IP

Request-log search MUST match persisted `client_ip` values.

#### Scenario: Search by client IP returns matching rows

- **WHEN** a request log row has `client_ip = "203.0.113.7"`
- **AND** the operator searches request logs for `203.0.113.7`
- **THEN** the matching request log row is returned
