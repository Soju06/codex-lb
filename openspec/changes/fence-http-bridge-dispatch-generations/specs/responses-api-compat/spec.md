## ADDED Requirements

### Requirement: Operation writes are fenced by durable dispatch generation

Every persisted HTTP-bridge dispatch MUST claim a monotonically increasing operation generation. Binding, appending, terminal settlement, finalization and dispatch cleanup MUST compare the captured generation and appropriate state, phase and response identity. The generation MUST NOT be refunded or reused, and MUST remain independent of recovery_dispatch_count and session owner_epoch.

#### Scenario: A predecessor dispatch loses operation authority

- **GIVEN** generation N was dispatched and N+1 has been claimed
- **WHEN** N attempts an operation write
- **THEN** the operation and its successor spool remain unchanged

#### Scenario: Session ownership advances while an operation completes

- **GIVEN** a detached dispatched predecessor still holds the current operation generation
- **WHEN** the session owner epoch advances and the predecessor completes
- **THEN** its own legal operation settlement can complete
- **AND** successor owner, continuation, turn-state and aliases remain unchanged

#### Scenario: A recovery claim is proven unsent

- **WHEN** a budgeted recovery claim is refunded before sending
- **THEN** the recovery budget may be restored once
- **AND** the dispatch generation remains unchanged

#### Scenario: An eligible operation is redispatched on a replacement account

- **GIVEN** existing request-scoped account selection permits replacement
- **AND** the operation remains in a legal redispatch phase
- **WHEN** the current session owner selects a replacement account
- **THEN** the dispatch claim atomically updates the operation account and increments its generation
- **AND** the preceding attempt cannot bind or append under the replacement generation

#### Scenario: Retention removes a historical operation

- **GIVEN** retention removes an operation and its dispatch generation
- **WHEN** the same logical request is registered again
- **THEN** the new operation row receives a fresh incarnation ID and starts at generation zero
- **AND** a delayed write using the removed operation ID cannot acquire the new row's authority
- **AND** retained operations still deduplicate by their scoped request fingerprint

#### Scenario: An accepted replay retains its public response identity

- **GIVEN** an output-free accepted response is safely replayed with a new dispatch generation
- **WHEN** the replacement upstream response completes under the original public lifecycle ID
- **THEN** operation binding and spool settlement use that same public response identity
- **AND** operation settlement is finalized under the replacement generation
- **AND** session continuity publication continues to use the actual upstream response identity and its existing owner guards
