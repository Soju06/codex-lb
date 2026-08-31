## ADDED Requirements

### Requirement: Pre-visible HTTP 429 does not establish a transient dispatch owner

Notwithstanding the account-bound retry requirement, a pre-visible HTTP 429
rejection MUST NOT establish a new dispatch-owner binding while owner
registration is pending. This exception MUST NOT clear or move an independently
established file, previous-response, turn-state, or existing dispatch owner.

#### Scenario: Pre-visible HTTP 429 does not establish a transient owner

- **GIVEN** a streaming Responses body is not a canonical account-neutral fresh
  replay
- **AND** the body has no independently established required account owner
- **WHEN** account A rejects the request with HTTP 429 before any response event
- **THEN** the proxy does not record account A as the dispatch owner
- **AND** normal failover selection may dispatch the body first on eligible
  account B

#### Scenario: Existing required owner remains fail-closed after HTTP 429

- **GIVEN** a streaming Responses body has a file, previous-response,
  turn-state, or existing dispatch owner on account A
- **WHEN** account A returns HTTP 429 before any response event
- **THEN** the proxy does not clear or move that owner
- **AND** the retained body is not dispatched on account B
