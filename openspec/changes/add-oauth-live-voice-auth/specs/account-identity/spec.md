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

Cache and singleflight keys MUST be a one-way digest over bearer and normalized `chatgpt-account-id`. Concurrent misses for the same pair MUST share one upstream validation. Positive entries MUST expire within 60 seconds and token expiry; credential-denial entries MUST expire within 5 seconds. Upstream availability and rate-limit failures MUST preserve typed failure semantics.

#### Scenario: Concurrent validation misses are coalesced

- **GIVEN** the same uncached bearer and normalized `chatgpt-account-id` reach OAuth identity validation concurrently
- **WHEN** upstream validation is still in flight
- **THEN** all callers await one shared validation
- **AND** cache keys and diagnostics expose no raw credential
