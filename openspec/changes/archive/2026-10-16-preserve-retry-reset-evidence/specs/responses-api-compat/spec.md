## ADDED Requirements

### Requirement: Retry health preserves upstream quota reset evidence

When HTTP bridge or direct WebSocket retry handling records a rate-limit or
quota failure, the service MUST retain valid upstream `resets_at` and
`resets_in_seconds` evidence for account-health settlement. Deferred keyed
health MUST retain the evidence until API-key reservation settlement completes.
Existing reset validation, missing-metadata fallbacks, required account
ownership, and replay safety requirements MUST remain enforced.

#### Scenario: A long reset permits existing owner retirement

- **GIVEN** a hard-affinity HTTP bridge owner receives a usage-limit rejection with a reset beyond the next request's recovery budget
- **WHEN** retry handling records that owner's health
- **THEN** the stored reset reflects the upstream deadline
- **AND** a later eligible request can use existing owner retirement and continue on a healthy replacement
- **AND** an explicit client anchor or a file pin does not authorize cross-account dispatch

#### Scenario: Keyed retry retains reset evidence until settlement

- **WHEN** a retryable upstream error with absolute or relative reset metadata arrives for a request with an unsettled API-key reservation
- **THEN** the deferred health penalty retains that metadata
- **AND** account health is written only after reservation settlement

#### Scenario: Direct WebSocket retries retain equivalent evidence

- **WHEN** a direct WebSocket pre-created retry or accepted output-free replay records upstream account health
- **THEN** it retains the parsed reset metadata from the original error frame
- **AND** existing file ownership and client-anchor restrictions remain enforced

#### Scenario: Invalid reset metadata retains fallback behavior

- **WHEN** an upstream limit error omits reset metadata or supplies invalid values, including JSON booleans
- **THEN** existing Retry-After or fallback cooldown behavior applies without a secondary parsing failure
