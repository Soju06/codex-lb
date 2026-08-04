## ADDED Requirements

### Requirement: Verified OAuth Live callers resolve to a stable principal

The system SHALL validate a supplied ChatGPT bearer and `chatgpt-account-id` against the upstream usage endpoint before trusting identity claims. It SHALL derive a stable principal from verified per-seat claims. That principal SHALL remain unchanged when a matching imported Account is added, removed, paused, or otherwise becomes ineligible. A matching imported Account MAY remain attached separately as caller integration metadata for usage and route selection. A credential without a stable claim SHALL require one unambiguous imported Account and MAY use that Account id as its fallback principal.

#### Scenario: External OAuth caller is accepted

- **GIVEN** valid OAuth credentials carry a stable seat claim and no imported Account matches them
- **WHEN** the caller reaches a Live Voice route
- **THEN** upstream credential validation succeeds through the configured default route
- **AND** the caller receives an independent stable principal

#### Scenario: Imported Account lifecycle preserves caller affinity

- **GIVEN** verified OAuth credentials carry a stable seat claim
- **WHEN** a matching imported Account is added, removed, paused, or otherwise becomes ineligible
- **THEN** the principal remains derived from the same stable seat claim
- **AND** caller Account metadata may change independently without changing the Live ownership scope

#### Scenario: Identity remains ambiguous

- **WHEN** credentials expose no stable seat claim and cannot resolve one eligible imported seat
- **THEN** identity resolution fails before policy lookup and upstream account selection
- **AND** the response reveals no identity or candidate details

### Requirement: OAuth identity validation is bounded and coalesced

Cache and singleflight keys MUST be a one-way digest over bearer and normalized `chatgpt-account-id`. Concurrent misses for the same pair MUST share one upstream validation. Each process MUST admit at most 32 distinct in-flight credential validations; excess distinct misses MUST fail with a typed rate-limit response before database or upstream work begins. When the final waiter for an unfinished validation disconnects, the validation task MUST be cancelled and MUST continue consuming one admission slot until cancellation has drained. Positive entries MUST expire within 60 seconds and token expiry; credential-denial entries MUST expire within 5 seconds. Upstream availability and rate-limit failures MUST preserve typed failure semantics.

#### Scenario: Concurrent validation misses are coalesced

- **GIVEN** the same uncached bearer and normalized `chatgpt-account-id` reach OAuth identity validation concurrently
- **WHEN** upstream validation is still in flight
- **THEN** all callers await one shared validation
- **AND** cache keys and diagnostics expose no raw credential

#### Scenario: Distinct validation capacity is exhausted

- **GIVEN** 32 distinct credential validations remain in flight on one process
- **WHEN** another uncached credential pair requests validation
- **THEN** the request fails with a typed rate-limit response
- **AND** no database or upstream validation begins for that pair

#### Scenario: The final waiter disconnects

- **GIVEN** one unfinished validation has no remaining request waiter
- **WHEN** its final waiter disconnects
- **THEN** the process cancels and drains the validation task
- **AND** the admission slot is released only after the task completes cancellation

### Requirement: Aggregate Codex usage requires imported Account membership

The OAuth-authenticated `/api/codex/usage` path SHALL authorize only a verified identity that resolves to one currently eligible imported Account. An independently verified external OAuth principal MAY use enabled OAuth Live routes but SHALL NOT receive the operator's aggregate local account-pool usage payload. Registered Proxy API Keys SHALL retain their existing usage behavior.

#### Scenario: External OAuth principal requests aggregate usage

- **GIVEN** valid OAuth credentials resolve to a stable principal without an eligible imported Account
- **WHEN** the caller requests `/api/codex/usage`
- **THEN** authorization fails before aggregate usage is read
- **AND** the same principal remains eligible for OAuth Live when the global policy permits it
