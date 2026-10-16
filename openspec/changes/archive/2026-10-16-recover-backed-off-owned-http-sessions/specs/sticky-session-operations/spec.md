## ADDED Requirements

### Requirement: Transient backoff preserves required owner availability

When a request is bound to one resolved account by hard continuation ownership,
the service MUST keep that account eligible for the existing transient-error
backoff fallback. The fallback MUST NOT authorize an account switch or clear
health penalties merely to select the owner. Persisted unavailability, account
scope, security policy, concurrency caps, and explicit caller exclusions MUST
remain enforced. Soft locality preferences MUST retain their existing ability
to select a healthy sibling instead.

#### Scenario: HTTP reconnect recovers on its backed-off owner after anchor denial

- **GIVEN** an HTTP conversation's proxy-injected anchor was rejected and its durable prefix metadata was cleared
- **AND** its ACTIVE owner is within bounded transient-error backoff while another account is healthy
- **WHEN** the client retries with a complete history on either Responses HTTP route
- **THEN** the request can select its original owner and complete
- **AND** the rejected anchor is not re-injected and the supplied history is not trimmed
- **AND** opaque reasoning and completed tool-call/output pairs remain unchanged on that owner
- **AND** the returned turn state supports a later successful continuation

#### Scenario: A separate session can use a healthy sibling

- **WHEN** an unbound request arrives while another account serves transient backoff
- **THEN** the selector can serve it on a healthy sibling without changing an existing conversation's hard owner

#### Scenario: Real unavailability still fails closed

- **WHEN** a required owner is quota-exceeded, paused, deactivated, or otherwise outside its existing eligibility constraints
- **THEN** transient backoff fallback does not admit that owner or silently move its account-bound history to a sibling

#### Scenario: Selection alone retains health evidence

- **WHEN** a required owner is selected through transient backoff fallback
- **THEN** its error counter and last-error timestamp remain intact until an existing recovery or outcome rule changes them
