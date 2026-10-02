## ADDED Requirements

### Requirement: Terminal-append bound leaves the durable append running

When the terminal transcript persistence bound expires, or the caller waiting
on it is cancelled, the event spooler MUST stop waiting and MUST NOT cancel the
terminal append task. The task MUST remain owned by the spooler until it
commits or fails. The caller MUST still receive the not-persisted,
settlement-required result within the bound. A late commit of that append MUST
NOT make the transcript replayable or rewrite a settled outcome.

#### Scenario: Bound expires while the append holds the SQLite writer

- **GIVEN** a terminal append holds a write transaction and is awaiting a statement
- **WHEN** the persistence bound expires
- **THEN** the caller receives a not-persisted, settlement-required result
- **AND** the append is not cancelled and runs to completion
- **AND** another connection can then take the SQLite writer slot

#### Scenario: Caller cancelled while the append holds the SQLite writer

- **GIVEN** a terminal append holds a write transaction and is awaiting a statement
- **WHEN** the caller waiting on the bound is cancelled
- **THEN** the cancellation propagates to the caller
- **AND** the append is not cancelled and runs to completion
- **AND** another connection can then take the SQLite writer slot
