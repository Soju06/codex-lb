## ADDED Requirements

### Requirement: Operation-fenced hard turns preserve client retry budget during cooldown

A hard turn-state HTTP bridge request arriving during retry-circuit cooldown MUST remain pending until cooldown expires only if an explicit server recovery mode is enabled, the request has not observed a response id or response event, and the bridge has a live durable session and owner epoch. The proxy MUST NOT dispatch upstream while waiting. After the wait, the request MUST pass through the existing durable operation-ledger admission before any `response.create` is sent.

#### Scenario: One-shot hard turn waits before durable arbitration

- **GIVEN** `server_anchored_replay_once` is enabled
- **AND** a turn-state-only hard continuation has a live durable owner
- **AND** its retry circuit is cooling down before submission
- **WHEN** the request reaches bridge startup
- **THEN** the proxy waits for the bounded cooldown instead of returning 503
- **AND** it sends no upstream request during the wait
- **AND** normal durable operation admission runs after cooldown

#### Scenario: Missing durable fence remains fail closed

- **GIVEN** a turn-state-only hard continuation has no durable session or owner
  epoch
- **WHEN** its retry circuit is cooling down
- **THEN** the proxy does not wait or dispatch upstream
- **AND** it returns the existing cooldown failure with a retry hint

#### Scenario: Operation ledger disabled remains fail closed

- **GIVEN** ambiguous continuation recovery mode is enabled
- **AND** a turn-state-only hard continuation has a live durable session and
  owner epoch
- **AND** the durable operation ledger is disabled
- **WHEN** its retry circuit is cooling down before submission
- **THEN** the proxy preserves the existing cooldown failure
- **AND** it does not wait or dispatch upstream

#### Scenario: Default mode remains fail closed

- **GIVEN** ambiguous continuation recovery mode is `fail_closed`
- **WHEN** any continuity-bound hard request arrives during cooldown
- **THEN** the proxy preserves the existing immediate cooldown failure
- **AND** it does not create or claim a durable recovery operation

#### Scenario: Request budget expires while waiting

- **GIVEN** an operation-fenced hard turn is allowed to wait through cooldown
- **AND** its request budget expires before the cooldown does
- **WHEN** the bounded wait reaches the request deadline
- **THEN** the proxy releases the request reservation and returns a terminal
  timeout
- **AND** it does not submit `response.create` after the deadline

#### Scenario: Cooldown waiter stays within the per-session queue limit

- **GIVEN** an operation-fenced hard turn is eligible to wait through cooldown
- **AND** the bridge session is already at its configured queue limit
- **WHEN** the request reaches the cooldown wait point before submission
- **THEN** the proxy rejects the request with the existing bridge queue full
  error
- **AND** it does not sleep or dispatch upstream

#### Scenario: Durable ownership is renewed while the cooldown wait is pending

- **GIVEN** an operation-fenced hard turn is waiting through startup cooldown
- **AND** the cooldown exceeds one durable lease refresh cadence
- **WHEN** the wait continues before submission
- **THEN** the proxy renews and revalidates the durable owner lease before the
  wait completes
- **AND** it fails closed if durable ownership changes during the wait
