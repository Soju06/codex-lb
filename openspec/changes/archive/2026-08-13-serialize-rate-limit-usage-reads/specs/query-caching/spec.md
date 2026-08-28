## ADDED Requirements

### Requirement: Aggregated rate-limit reads never run concurrently on a shared session

Proxy rate-limit header and usage-payload construction MUST NOT execute
multiple statements concurrently on one `AsyncSession`. Repository objects
exposed by the same `ProxyRepositories` context SHALL be treated as sharing that
single-session ownership constraint.

#### Scenario: Rate-limit header reads execute sequentially

- **WHEN** the proxy constructs upstream-quota rate-limit headers from primary, secondary, monthly, and credit usage rows
- **THEN** each database read MUST complete before the next read starts on the shared session
- **AND** the returned header names and values remain unchanged for equivalent rows

#### Scenario: Codex usage payload reads execute sequentially

- **WHEN** the proxy constructs the aggregate `/api/codex/usage` payload for a request that does not resolve to a codex-lb API key, using usage windows, credits, and additional limits
- **THEN** each database read MUST complete before the next read starts on the shared session
- **AND** the returned payload remains schema- and value-compatible for equivalent rows
