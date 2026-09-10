## ADDED Requirements

### Requirement: Request-local unavailability of a soft TTL-bounded owner is non-mutating

When a soft TTL-bounded (`prompt_cache`) mapping resolves to an owner that is absent from the request's selectable candidates only because of request-local pressure, selection MUST serve the request from an alternate eligible account without deleting or rebinding the mapping, and MUST emit the internal `internal_soft_affinity_spillover` diagnostic; when the request asks for sensitive-detail redaction, that diagnostic MUST NOT include the owner or alternate account identifiers. Request-local pressure is exactly: (a) the owner is in the request's routable pool but filtered by the per-account concurrency caps, or (b) the owner is in the request's excluded set (this request's retry loop already failed over from it after a transient upstream failure) while it remains in the request's continuity-owner scope with a persisted status of `active`, `reauth_required`, `rate_limited` or `quota_exceeded`. The alternate MUST apply only to that request; a later request on the same mapping MUST return to the owner once it is selectable. Selection MUST still rebind the mapping to the fallback, as before, when the owner is in the overload isolation stage, when the request explicitly reallocates the mapping, when the owner's persisted status is `paused` or `deactivated`, or when the owner is outside the request's continuity-owner scope (removed, out of API-key scope, or not authorized for a security-work request). Hard mappings and durable `codex_session` mappings without a TTL keep their existing rules.

#### Scenario: Capped prompt-cache owner spills without rebinding

- **GIVEN** a `prompt_cache` thread mapping points to account A
- **AND** account A is at its per-account stream cap
- **AND** account B is eligible and below cap
- **WHEN** a request on that thread selects an account with a stream lease
- **THEN** account B is selected for this request
- **AND** the mapping still points to account A and no sticky row is written or deleted
- **AND** `internal_soft_affinity_spillover` is emitted
- **AND** once account A's cap clears the next request on that thread selects account A

#### Scenario: Owner excluded after a transient upstream failure spills without rebinding

- **GIVEN** a `prompt_cache` thread mapping points to account A whose persisted status is `active`
- **AND** the request's retry loop excluded account A after a transient upstream failure on this turn
- **AND** account B is eligible
- **WHEN** the retry re-selects an account with account A excluded
- **THEN** account B is selected for this request
- **AND** the mapping still points to account A and no sticky row is written or deleted
- **AND** the next turn on that thread, with no exclusion, selects account A

#### Scenario: Spillover diagnostic honours request redaction

- **GIVEN** a `prompt_cache` thread mapping points to account A and the request has excluded account A after a transient failure
- **AND** the request asks for sensitive-detail redaction
- **WHEN** the request selects an account and spills to account B
- **THEN** `internal_soft_affinity_spillover` is emitted with both account identifiers redacted

#### Scenario: Isolated capped owner is still rebound

- **GIVEN** a `prompt_cache` thread mapping points to account A, which is at its stream cap and in the overload isolation stage
- **AND** account B is eligible and not in overload backoff
- **WHEN** a request on that thread selects an account
- **THEN** account B is selected and the mapping is rebound to account B

#### Scenario: Explicit reallocation still rebinds a capped owner

- **GIVEN** a `prompt_cache` thread mapping points to account A, which is at its stream cap
- **WHEN** a request on that thread selects an account with sticky reallocation requested
- **THEN** the mapping is rebound to the selected alternate

#### Scenario: Excluded owner outside the continuity scope is rebound

- **GIVEN** a `prompt_cache` thread mapping points to account A, which is not authorized for security work
- **AND** the request requires security-work authorization and has excluded account A
- **WHEN** the request selects an account
- **THEN** the authorized alternate is selected and the mapping is rebound to it

#### Scenario: Excluded paused owner is rebound

- **GIVEN** a `prompt_cache` thread mapping points to account A whose persisted status is `paused`
- **AND** the request has excluded account A
- **WHEN** the request selects an account
- **THEN** the alternate is selected and the mapping is rebound to it

#### Scenario: Bare process-session owner without a TTL keeps persisting its fallback

- **GIVEN** a bare `codex_session` mapping without a TTL points to account A
- **AND** the request has excluded account A
- **WHEN** the request selects an account
- **THEN** the alternate is selected and the mapping is rebound to it, as today
