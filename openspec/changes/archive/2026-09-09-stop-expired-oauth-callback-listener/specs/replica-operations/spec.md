## ADDED Requirements

### Requirement: Browser OAuth callback listeners expire without follow-up requests

Each replica SHALL schedule cleanup for locally started or hydrated pending browser OAuth flows. When the final pending browser flow expires, the replica MUST prune the expired local state and release its process-local callback listener without requiring another request. While an unexpired pending browser flow remains, expiry cleanup MUST retain the shared listener.

#### Scenario: Abandoned browser flow expires

- **GIVEN** one pending browser flow with a running callback listener
- **WHEN** its TTL elapses without any subsequent request or callback
- **THEN** the expired flow and its state-token lookup are removed
- **AND** the process-local callback port is released

#### Scenario: Overlapping browser flows expire

- **GIVEN** pending browser flows with different deadlines sharing a listener
- **WHEN** the earliest flow expires
- **THEN** the listener remains available for the later flow
- **AND** it is released after the final flow expires without a follow-up request

#### Scenario: A pending browser flow is hydrated

- **GIVEN** a replica loads a pending browser flow from durable storage
- **WHEN** its deadline is earlier than the currently scheduled wake-up
- **THEN** the replica recalculates the expiry wake-up for that earlier deadline
- **AND** removes the expired local state without a later request

#### Scenario: Store reset drains expiry work

- **GIVEN** the store owns a browser-flow expiry task
- **WHEN** the store is reset
- **THEN** its expiry task is cancelled and awaited before reset returns
