## ADDED Requirements

### Requirement: Continue eligible transcript backlog persistence

Background HTTP bridge transcript persistence MUST continue processing eligible queued events after a bounded pass without waiting for new events or another flush interval. Each pass MUST give every eligible operation a bounded batch opportunity before revisiting an operation. Terminal persistence, owner fencing, queue limits, failure settlement and shutdown cancellation guarantees MUST remain unchanged.

#### Scenario: Burst exceeds one batch

- **GIVEN** one operation has several batches of nonterminal events queued
- **WHEN** the background writer successfully persists the first batch and no new events arrive
- **THEN** it continues persisting the remaining eligible batches without interval waits
- **AND** it preserves event order and the configured maximum batch size

#### Scenario: Multiple operations have backlog

- **GIVEN** two operations have eligible queued events
- **WHEN** background persistence drains their backlog
- **THEN** each pass offers a bounded batch to both operations before revisiting either operation

#### Scenario: Backlog drain is cancelled

- **GIVEN** background persistence is waiting for a writer with more events queued
- **WHEN** the batcher closes
- **THEN** it cancels and awaits the flusher without starting another batch
