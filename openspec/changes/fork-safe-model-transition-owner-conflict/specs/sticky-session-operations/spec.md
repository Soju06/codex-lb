## MODIFIED Requirements

### Requirement: Hard continuity remains owner-bound and bounded

Requests that depend on hard continuity MUST remain owner-bound and fail closed
when independently resolved owners conflict. The sole model-transition
exception is the proof-gated HTTP bridge child lane defined by
`responses-api-compat`: it is limited to a local request with the exact
`continuity_owner_conflict` error, no previous-response or resolved file owner,
and an account-neutral effective payload. That child lane MUST clear aliases,
exclude the failed owner, and leave persisted hard mappings unchanged.

#### Scenario: Non-model-transition hard owner conflict fails closed

- **GIVEN** independently resolved hard sources identify different accounts
- **AND** the request is not the guarded model-transition case
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict` before upstream
  dispatch
- **AND** source ordering does not choose either owner

#### Scenario: Guarded model-transition exception does not rewrite hard mappings

- **GIVEN** a request satisfies the account-neutral model-transition child-lane
  proof
- **WHEN** the child lane is created
- **THEN** persisted hard aliases remain unchanged
- **AND** the failed owner is excluded only for that request
