## ADDED Requirements

### Requirement: Pre-visible rate-limit failover reallocates soft affinity

When a streaming Responses request receives a pre-visible upstream rate-limit
or quota failure, the proxy MUST exclude the failed account from the remaining
attempts and reallocate soft prompt-cache or sticky-thread affinity before
selecting a replacement. This applies to self-contained inline-image input as
well as text input. It MUST NOT relax an independently resolved required owner,
including a `previous_response_id`, turn-state owner, or live uploaded-file
owner.

#### Scenario: Inline image retries after a prompt-cache account rate limit

- **GIVEN** a self-contained inline-image streaming request has prompt-cache affinity to account A
- **AND** account A returns a pre-visible upstream `429` usage-limit error
- **AND** account B is eligible for the same model
- **WHEN** the proxy retries the request
- **THEN** account A is excluded from the remaining attempts
- **AND** prompt-cache affinity is reallocated before selecting account B
- **AND** the client receives account B's successful stream

#### Scenario: Required file ownership remains fail closed after a rate limit

- **GIVEN** a streaming request is pinned to account A by a live uploaded file owner
- **AND** account A returns a pre-visible upstream `429` usage-limit error
- **WHEN** the proxy evaluates retry
- **THEN** it MUST NOT replay the request on another account
