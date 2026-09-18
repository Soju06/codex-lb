## ADDED Requirements

### Requirement: Verified full-history HTTP fallback can release turn ownership

When HTTP streaming bypasses the bridge, the service MAY remove bridge affinity
headers only when durable metadata in the same API-key scope proves a full
resend, the request has no explicit previous_response_id or file owner, and the
entire payload is account-neutral. The durable turn-state owner and required
owner lookup MUST agree. Forwarded owner requests MUST NOT use this fallback.
Incomplete or unverified history MUST retain its ownership constraints.

#### Scenario: Quota rejection after verified HTTP bypass

- **WHEN** a verified account-neutral full resend bypasses the bridge
- **AND** the first account rejects it with HTTP 429 before output
- **THEN** another eligible account can complete the request
- **AND** the replacement receives no bridge affinity from the retired account

#### Scenario: Incomplete history retains ownership

- **WHEN** a bypass request cannot prove full history
- **THEN** the request retains its required account

#### Scenario: Optional durable verification is unavailable

- **WHEN** the optional durable full-resend lookup fails
- **THEN** the service preserves bridge affinity and continues normal HTTP fallback
- **AND** required owner resolution remains authoritative and fail-closed

#### Scenario: Explicit ownership retains ownership

- **WHEN** a bypass request references previous_response_id, a file owner, or an account-owned item
- **THEN** the request retains its required account
