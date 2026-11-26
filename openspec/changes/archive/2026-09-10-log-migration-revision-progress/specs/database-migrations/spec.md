## ADDED Requirements

### Requirement: Online migration revision progress

Online upgrades SHALL log each revision identifier and direction before execution and SHALL log elapsed seconds when execution completes or fails. Progress SHALL NOT include SQL, parameters, database URLs, revision descriptions, or exception messages. Execution completion SHALL NOT claim transaction commit. Failures SHALL propagate without executing later revisions. An upgrade with no pending revisions SHALL NOT emit revision execution events.

#### Scenario: Pending revisions execute

- **WHEN** an online upgrade executes pending revisions
- **THEN** each revision has an ordered start and execution-complete event with nonnegative elapsed seconds
- **AND** the start event is available before the revision runs

#### Scenario: A revision fails

- **WHEN** revision execution raises an exception
- **THEN** a failure event identifies the revision and elapsed seconds without the exception text
- **AND** the original failure propagates and no later revision starts

#### Scenario: Database is current

- **WHEN** an upgrade has no pending revisions
- **THEN** no revision start or completion events are emitted
