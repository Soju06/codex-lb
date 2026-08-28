## ADDED Requirements

### Requirement: Fleet observability requires API key authentication

The system SHALL expose `GET /api/fleet/observability` for trusted local fleet
consumers. The route MUST require a valid Bearer API key even when global proxy
API-key authentication is disabled.

#### Scenario: Missing fleet observability key is rejected

- **WHEN** a client calls `GET /api/fleet/observability` without a Bearer token
- **THEN** the system returns 401
- **AND** no observability payload is returned

### Requirement: Fleet observability reports pressure windows

The system SHALL return read-only Codex pressure windows for the last 30 minutes
and last 2 hours. Each window SHALL include total request count, error count,
input tokens, cached input tokens, output tokens, cost, account breakdown,
request-kind breakdown, and client-group breakdown.

#### Scenario: Valid key returns pressure windows

- **WHEN** a client calls `GET /api/fleet/observability` with a valid Bearer API key
- **THEN** the response includes `pressure.windows[]` entries for `30m` and `2h`
- **AND** warmup traffic and soft-deleted request logs are excluded
- **AND** account-scoped keys only include logs for assigned accounts

### Requirement: Fleet observability reports sticky-session continuity

The system SHALL return read-only sticky-session distribution by account and
kind. Prompt-cache pins older than the configured cache affinity TTL SHALL count
as stale; other sticky-session kinds SHALL not count as stale.

#### Scenario: Valid key returns sticky distribution

- **WHEN** sticky sessions exist for accounts visible to the key
- **THEN** the response includes `sticky.total`, `sticky.recentCount`,
  `sticky.staleCount`, and `sticky.byAccount[]`
- **AND** account-scoped keys only include sticky sessions for assigned accounts

### Requirement: Fleet observability excludes sensitive data

Fleet observability responses MUST NOT include prompt contents, raw request IDs,
archive request IDs, session IDs, sticky-session keys, client IP addresses, API
key identifiers, request error messages, auth tokens, or raw credential data.

#### Scenario: Sensitive fields are omitted

- **WHEN** a valid client calls `GET /api/fleet/observability`
- **THEN** no response object includes raw request identifiers, session
  identifiers, sticky-session keys, client IP addresses, API key identifiers,
  prompt contents, token fields, or raw error payloads

### Requirement: Fleet observability follows fleet usage visibility policy

The endpoint SHALL reuse the fleet summary account scoping and usage visibility
policy. If the authenticated key cannot view account-pool usage, the endpoint
SHALL return a successful non-sensitive payload with no pressure windows and no
sticky-session account distribution.

#### Scenario: Usage visibility disabled

- **WHEN** a valid API key does not include `account_pool_usage`
- **OR** the global API-key quota privacy setting hides upstream quota data
- **THEN** `GET /api/fleet/observability` returns 200
- **AND** the response does not expose request pressure or sticky-session
  account distribution
