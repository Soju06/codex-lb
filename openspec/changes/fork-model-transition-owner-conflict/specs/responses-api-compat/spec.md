# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge model-transition owner conflicts fork a guarded child lane

When a hard continuity lookup identifies a durable owner for an incompatible model and HTTP bridge creation returns `continuity_owner_conflict`, the service MUST retry at most once on a new account-neutral child lane when the request is local, has no `previous_response_id`, has no resolved file owner, and the effective payload passes the account-neutral fresh-replay validator. The child lane MUST exclude the conflicting owner, force local creation, use hard affinity, and clear parent-derived affinity, hard-continuity, and reused turn-state identity before submitting the request.

#### Scenario: Eligible model transition conflict forks once

- **GIVEN** a durable hard continuity key belongs to account A for the old model
- **AND** the new request uses an incompatible model with account-neutral input
- **WHEN** HTTP bridge creation returns `continuity_owner_conflict`
- **THEN** the service retries once on a new hard account-neutral lane
- **AND** the retry excludes account A and does not forward the request

#### Scenario: Child lane does not inherit parent identity

- **GIVEN** the model-transition request reuses the parent turn-state header
- **WHEN** the service forks to the account-neutral child lane
- **THEN** the submitted child request has no parent session id
- **AND** it has no parent affinity policy or hard-continuity anchor

#### Scenario: Second conflict remains fail-closed

- **GIVEN** the service already forked once for the model-transition request
- **WHEN** the child lane also returns `continuity_owner_conflict`
- **THEN** the service returns that error without creating another child lane
