## ADDED Requirements

### Requirement: Operation-fenced hard turns preserve client retry budget during cooldown

When a hard turn-state HTTP bridge request arrives during retry-circuit cooldown,
the proxy MUST keep the request pending until cooldown expires only if an
explicit server recovery mode is enabled, the request has not observed a
response id or response event, and the bridge has a live durable session and
owner epoch. The proxy MUST NOT dispatch upstream while waiting. After the wait,
the request MUST pass through the existing durable operation-ledger admission
before any `response.create` is sent.

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
