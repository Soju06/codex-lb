## ADDED Requirements

### Requirement: Assignment cutover recovery preserves hard-ownership safety
An API-key account-assignment cutover MUST NOT weaken hard ownership by directly sending `previous_response_id`, account-scoped files, encrypted turn state, or opaque conversation state to a different account. The service MAY move only a request that the retained full-resend evidence proves can be reconstructed as a fresh account-neutral request. When that proof is unavailable, the permanent cutover failure MUST terminate with `continuity_reset_required` instead of entering the ordinary retryable owner-unavailable loop.

#### Scenario: cross-account recovery requires a verified fresh projection
- **GIVEN** the account that owns a hard continuation is no longer assigned to the API key
- **WHEN** a currently assigned account is considered for recovery
- **THEN** the service uses that account only if the request can be projected into a fresh account-neutral replay
- **AND** no old owner anchor or session-affinity header reaches the replacement account

#### Scenario: unsafe cutover remains fail-closed
- **GIVEN** hard continuity cannot be reconstructed without account-scoped state
- **WHEN** the owner is permanently excluded by an assignment cutover
- **THEN** the request fails closed before replacement-account dispatch
- **AND** the failure is terminal and actionable rather than retryable
