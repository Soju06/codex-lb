# responses-api-compat Delta

## REMOVED Requirements

### Requirement: Fenced one-shot recovery dispatch

**Reason**: The requirement MUSTs a durable one-shot replay budget that is "consumed atomically when a replay is claimed for dispatch". `drop-bridge-recovery-modes` deleted the only claimant; the primitive that implemented the claim (`DurableBridgeRepository.claim_unknown_operation_for_recovery`, its `max_recovery_dispatches` bound and the `restore_recovery_dispatch_claim` refund on `mark_operation_unknown`) has had zero production callers since, so nothing can consume, restore or settle the budget. All three of its scenarios drive that deleted claim path, and its own title states the deleted dispatch, so a MODIFIED block cannot express the removal.

**Migration**: None. No supported configuration could claim a replay after `drop-bridge-recovery-modes`: an `unknown` operation already returns the `upstream_operation_status_unknown` 503 with a cooldown `Retry-After` unconditionally. The `http_bridge_operations.recovery_dispatch_count` column is retained for one release for rolling-upgrade safety and its ORM mapping is retired; the Alembic drop is queued in `openspec/specs/deployment-installation/context.md`.

## MODIFIED Requirements

### Requirement: Terminal append failure preserves authoritative settlement

When durable append of a terminal HTTP-bridge event raises after the operation was acknowledged, the proxy MUST attempt to persist the intended terminal operation state through the same operation, session, instance, and owner-epoch fence. Cancellation MUST be deferred through the append and any required fallback settlement. The event spool MUST remain incomplete, and the persistence failure MUST NOT replace or block the terminal event and end-of-stream marker already selected for downstream delivery. A rejected or failed fallback settlement MUST be logged and MUST NOT bypass the owner fence or overwrite a newer operation attempt admitted under the same owner epoch. That newer-attempt rejection MUST rest on the operation's own state and persisted upstream-response identity; no durable per-attempt dispatch counter is kept for it. The durable operation model MUST NOT map a retired recovery-dispatch counter column. Such a column MAY remain in the physical schema, allow-listed by the schema-drift gate, until the release after the last release whose ORM mapped it, because a supported previous-release replica still writes it while the migration Job runs ahead of the workload roll.

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
- **THEN** the fallback is rejected by the operation-state and persisted upstream-response identity fence
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

#### Scenario: Retired recovery-dispatch column stays insertable during the rolling upgrade

- **GIVEN** a database at the current Alembic head
- **WHEN** a replica running the previous release inserts a durable operation row with an explicit recovery-dispatch counter value
- **THEN** the insert succeeds because the physical column still exists
- **AND** the current release's operation model does not map that column, so its own inserts take the column default
- **AND** the schema-drift check reports no drift for the retained column
