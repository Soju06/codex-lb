## ADDED Requirements

### Requirement: Terminal append failure preserves authoritative settlement

When durable append of a terminal HTTP-bridge event raises after the operation was acknowledged, the proxy MUST attempt to persist the intended terminal operation state through the same operation, session, instance, and owner-epoch fence. Cancellation MUST be deferred through the append and any required fallback settlement. The event spool MUST remain incomplete, and the persistence failure MUST NOT replace or block the terminal event already selected for downstream delivery. A rejected or failed fallback settlement MUST be logged and MUST NOT bypass the owner fence or overwrite a newer operation attempt admitted under the same owner epoch.

#### Scenario: Terminal append exception settles the current owner operation

- **GIVEN** an acknowledged HTTP-bridge operation owned by the current session epoch
- **WHEN** durable terminal-event append raises
- **THEN** the operation is persisted in the intended terminal state
- **AND** its event spool remains incomplete
- **AND** reconnect or recovery does not observe the operation as acknowledged work

#### Scenario: Stale owner cannot settle after terminal append exception

- **GIVEN** an HTTP-bridge operation whose owner epoch has advanced
- **WHEN** the stale batcher encounters a terminal-event append exception
- **THEN** fallback settlement is rejected by the durable owner fence
- **AND** the stale batcher does not mutate the operation state

#### Scenario: Newer retry rejects delayed fallback settlement

- **GIVEN** terminal append committed its operation state before reporting an exception
- **AND** a retry under the same owner epoch has since reset the operation to submitted
- **WHEN** fallback settlement for the prior attempt runs
- **THEN** the fallback is rejected by an operation-state and persisted upstream-response identity fence
- **AND** the newer submitted attempt remains unchanged

#### Scenario: Replay alias preserves the acknowledged-attempt fence

- **GIVEN** a replay whose client-visible response alias differs from its persisted upstream response ID
- **WHEN** durable terminal-event append raises
- **THEN** fallback settlement compares the acknowledged or already terminal operation against the persisted upstream response ID
- **AND** persists the intended client-visible terminal response ID

#### Scenario: Successful terminal append remains atomic and replayable

- **WHEN** durable terminal-event append succeeds
- **THEN** the terminal event and intended operation state are persisted atomically
- **AND** the completed event spool remains eligible for replay
