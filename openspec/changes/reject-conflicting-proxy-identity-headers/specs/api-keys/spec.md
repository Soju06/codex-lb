## ADDED Requirements

### Requirement: Proxy identity cannot bypass disabled API-key auth

When API-key authentication is disabled, protected HTTP and WebSocket routes MUST use raw-peer-backed locality consensus. Any `proxy_unauthenticated_client_cidrs` exception MUST evaluate only the launcher-preserved raw socket peer and MUST fail closed when that peer is unavailable. A projected client identity MUST NOT satisfy the socket allowlist.

#### Scenario: Agreeing identities retain local access

- **WHEN** a trusted raw peer sends populated identity families that all resolve to loopback
- **AND** the request otherwise satisfies local-request rules
- **THEN** `/v1/models` and `/v1/responses` may proceed without an API key

#### Scenario: Conflict cannot authorize HTTP or WebSocket

- **WHEN** populated identity families resolve differently or one cannot be resolved
- **AND** the raw peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** `/v1/models` is rejected with HTTP 401
- **AND** `/v1/responses` WebSocket is rejected with HTTP 401 before upgrade

#### Scenario: Projected allowlist identity is rejected

- **WHEN** the projected client belongs to `proxy_unauthenticated_client_cidrs` but the preserved raw peer does not
- **THEN** the protected route is rejected with HTTP 401
