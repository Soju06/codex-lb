## ADDED Requirements

### Requirement: Bounded bridge queues use injected lifecycle timing

The bounded HTTP-bridge live queue SHALL use its owner's scheduler for spawned
producer and cleanup tasks. Queue enqueue deadlines SHALL use the owner's
clock and scheduler. Live read timeout scopes SHALL run in the consuming task,
including when the timeout is nonpositive, and SHALL preserve raced payloads.
The timing guard SHALL require explicit collaborators at production calls to
the queue constructor and enqueue, deferred-enqueue, and next-event helpers.

#### Scenario: Paused queue expires under virtual time

- **WHEN** the injected clock advances through a paused request's own deadline
- **THEN** its blocked enqueue SHALL revoke that request's queue and return
- **AND** the shared reader SHALL resume sibling lifecycle settlement
- **AND** queued payload credits SHALL remain accounted until read or discarded

#### Scenario: Read timeout races publication

- **WHEN** a live queue publication races its consuming task's timeout
- **THEN** the read SHALL deliver the payload or retain it for the next read
- **AND** it SHALL NOT spawn a child task to consume the payload
- **AND** completion or cancellation SHALL release the timeout's timer
