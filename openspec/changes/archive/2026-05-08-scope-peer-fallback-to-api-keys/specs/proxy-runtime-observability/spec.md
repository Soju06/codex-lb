## MODIFIED Requirements

### Requirement: Proxy supports optional peer fallback before downstream output
The service MUST support an opt-in peer fallback path for authenticated API key proxy requests when the local `codex-lb` process is alive, accepted the request, and cannot complete the local proxy attempt before any downstream-visible output starts. This behavior MUST NOT be treated as process-down failover, health-check failover, load-balancer failover, or automatic replacement for a stopped local process. Peer fallback is enabled for a request only when the authenticated API key has at least one peer fallback base URL configured.

#### Scenario: Local pre-output failure falls back to an API key peer
- **GIVEN** the authenticated API key has at least one peer fallback base URL configured
- **AND** the local process is alive and handling the request
- **WHEN** the local proxy attempt fails or times out before sending response headers, body bytes, SSE events, websocket messages, or any other downstream-visible output
- **THEN** the service attempts the request through a peer URL configured on that API key
- **AND** the client receives either the peer response or a stable fallback failure from the local service

#### Scenario: Local upstream rate limit falls back before output
- **GIVEN** the authenticated API key has at least one peer fallback base URL configured
- **WHEN** all local account attempts produce a pre-output upstream rate-limit or quota failure such as `rate_limit_exceeded`, `usage_limit_reached`, `insufficient_quota`, `usage_not_included`, or `quota_exceeded`
- **THEN** the service attempts the request through a peer URL configured on that API key
- **AND** it does not require the local failure to be rewritten to `no_accounts` before fallback is considered

#### Scenario: Process-down failover is out of scope
- **WHEN** the local `codex-lb` process is stopped, unreachable, or unable to accept the client connection
- **THEN** this peer fallback feature does not apply
- **AND** any process-down routing remains the responsibility of external supervisors, service discovery, or load balancers

#### Scenario: Missing API key peer assignments preserve local behavior
- **GIVEN** the request is unauthenticated or its authenticated API key has no peer fallback base URLs
- **WHEN** the local proxy attempt cannot complete before downstream output starts
- **THEN** the service returns the existing local failure behavior without attempting a peer fallback
