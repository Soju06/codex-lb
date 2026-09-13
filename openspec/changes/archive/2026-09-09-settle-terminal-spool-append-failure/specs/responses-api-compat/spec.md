## ADDED Requirements

### Requirement: Terminal append failure preserves authoritative settlement

When durable append of a terminal HTTP-bridge event raises after the operation was acknowledged, the proxy MUST attempt to persist the intended terminal operation state through the same operation, session, instance, and owner-epoch fence. Cancellation MUST be deferred through the append and any required fallback settlement. The event spool MUST remain incomplete, and the persistence failure MUST NOT replace or block the terminal event and end-of-stream marker already selected for downstream delivery. A rejected or failed fallback settlement MUST be logged and MUST NOT bypass the owner fence or overwrite a newer operation attempt admitted under the same owner epoch.

#### Scenario: Terminal append exception settles the current owner operation

- **GIVEN** an acknowledged HTTP-bridge operation owned by the current session epoch
- **WHEN** durable terminal-event append raises
- **THEN** the operation is persisted in the intended terminal state
- **AND** its event spool remains incomplete
- **AND** the terminal event and end-of-stream marker are queued before fallback settlement can stall
- **AND** reconnect or recovery does not observe the operation as acknowledged work

#### Scenario: Grouped failures deliver every sibling before settlement

- **GIVEN** one upstream error selects terminal failures for multiple pending operations
- **WHEN** the first operation's fallback settlement stalls
- **THEN** every selected operation attempts its owner-fenced terminal append before any terminal queue is exposed
- **AND** every selected operation then receives its terminal event and end-of-stream marker before fallback settlement
- **AND** sibling delivery does not wait for the first fallback settlement
- **AND** cancellation is preserved as the final outcome only after every pre-delivered sibling finishes settlement and finalization
- **AND** one sibling's finalization failure does not prevent later siblings from settling or replace pending cancellation

#### Scenario: Cancellation preserves terminal delivery authority

- **GIVEN** terminal append finishes while relay cancellation is deferred
- **WHEN** the append result becomes available
- **THEN** the terminal event and end-of-stream marker are queued
- **AND** a completed-delivery scope is marked authoritative before cleanup can deactivate it
- **AND** cancellation during that delivery-authority claim does not skip required fallback settlement
- **AND** cancellation is preserved only after delivery and required settlement

#### Scenario: Stale owner cannot settle after terminal append exception

- **GIVEN** an HTTP-bridge operation whose owner epoch has advanced
- **WHEN** the stale batcher encounters a terminal-event append exception
- **THEN** fallback settlement is rejected by the durable owner fence
- **AND** the stale batcher does not mutate the operation state

#### Scenario: Newer retry rejects delayed fallback settlement

- **GIVEN** terminal append committed its operation state before reporting an exception
- **AND** a retry under the same owner epoch has since reset the operation to submitted
- **WHEN** fallback settlement for the prior attempt runs
- **THEN** the fallback is rejected by an immutable recovery-attempt generation plus operation-state and persisted upstream-response identity fence
- **AND** the newer submitted attempt remains unchanged

#### Scenario: Replay alias preserves the acknowledged-attempt fence

- **GIVEN** a replay whose client-visible response alias differs from its persisted upstream response ID or whose active upstream response ID was reset before a replacement response was created
- **WHEN** durable terminal-event append raises
- **THEN** fallback settlement compares the acknowledged or already terminal operation against every response identity that may remain persisted when a replacement acknowledgement update fails
- **AND** persists the intended client-visible terminal response ID when present
- **AND** otherwise preserves the known upstream response ID

#### Scenario: Successful terminal append remains atomic and replayable

- **WHEN** durable terminal-event append succeeds
- **THEN** the terminal event and intended operation state are persisted atomically
- **AND** the completed event spool remains eligible for replay
