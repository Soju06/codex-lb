## ADDED Requirements

### Requirement: Ordinary transcript enqueue remains memory-only during append

Non-terminal HTTP bridge transcript enqueue with unchanged session, instance,
owner epoch, and recovery generation MUST NOT wait for an in-flight durable
append. Context validation and bounded queue insertion MUST be atomic. Owner
or recovery changes MUST retain serialization with in-flight appends, and
terminal draining MUST wait for all accepted events to persist in order.

#### Scenario: Ordinary event arrives during a durable append

- **GIVEN** a durable append is blocked for an operation
- **WHEN** a non-terminal event arrives with the same owner and generation
- **THEN** enqueue completes before the durable append is released
- **AND** the event persists after the earlier batch once append resumes

#### Scenario: Owner changes during a durable append

- **GIVEN** a durable append is blocked for an operation
- **WHEN** an enqueue changes its owner context
- **THEN** the rebind waits for the in-flight append before changing context

#### Scenario: Terminal event arrives with pending append work

- **GIVEN** an earlier append is blocked and another event is queued
- **WHEN** terminal enqueue begins
- **THEN** it waits until the append and queued events are persisted in order
- **AND** only then finalizes the spool
