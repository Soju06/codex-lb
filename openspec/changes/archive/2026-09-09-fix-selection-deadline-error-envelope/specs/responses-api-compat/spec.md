# responses-api-compat Delta

## ADDED Requirements

### Requirement: Hard-affinity local selection failures preserve their error envelope at the request deadline

When Responses account selection resolves a hard affinity owner outside the request's authenticated account or security policy scope, the proxy MUST treat that scope mismatch as non-recoverable within the request. It MUST skip account-capacity recovery waits, avoid upstream dispatch, and emit the established hard-affinity error envelope exactly once, preserving `hard_affinity_saturated` unless an existing stronger required-owner envelope applies. It MUST NOT rewrite this local ownership failure as `upstream_request_timeout` by repeatedly retrying an ineligible owner until the deadline. The selection MUST leave no account lease or owner-row mutation behind.

Scope mismatch MUST be derived from the authenticated policy pool before health, model eligibility, retry exclusions, and concurrency filtering. Temporary unavailability of an in-scope owner MUST NOT be treated as a scope mismatch.

Genuine budget exhaustion during an upstream connection, token refresh, or stream attempt MUST continue to surface `upstream_request_timeout`. Soft-affinity capacity waits and eligible local-capacity recovery MUST retain their existing retry and terminal classifications.

#### Scenario: API-key-scoped hard owner fails closed at selection deadline

- **GIVEN** a durable hard Codex-session owner outside the API-key assignment scope
- **AND** no assigned account can satisfy the hard ownership constraint
- **WHEN** selection resolves that out-of-scope owner, including near the request deadline
- **THEN** the route emits one `hard_affinity_saturated` error envelope
- **AND** it performs no capacity recovery wait or retry
- **AND** it does not dispatch upstream or retire/rebind the durable owner

#### Scenario: Upstream work timeout remains distinct

- **GIVEN** account selection succeeds within the request budget
- **WHEN** an upstream connect or stream attempt consumes the remaining budget
- **THEN** the route emits `upstream_request_timeout`

#### Scenario: Eligible local capacity keeps recovery semantics

- **GIVEN** selection reports a recoverable local account-capacity condition and another account can become eligible
- **WHEN** the request remains within its budget
- **THEN** the route waits and retries as before
- **AND** a terminal local-capacity error is not rewritten as a hard-affinity failure

#### Scenario: In-scope owner remains recoverable

- **GIVEN** a hard owner within authenticated policy scope is temporarily unavailable or excluded for a retry
- **WHEN** selection returns `hard_affinity_saturated`
- **THEN** the existing bounded owner-recovery behavior remains available
- **AND** the owner is neither rebound nor marked out of scope
