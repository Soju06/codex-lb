## ADDED Requirements

### Requirement: Durable transcript cleanup exposes bounded aggregate telemetry

Each durable operation transcript cleanup pass MUST expose low-cardinality
telemetry for pass duration, deleted-operation count, completion outcome, and
whether the pass stopped with likely eligible backlog remaining. Logs and
metrics MUST NOT include prompt/output text, operation identifiers, session
keys, account identifiers, or model names.

If a later batch fails after earlier batches committed, failure telemetry MUST
include the operations deleted and batches completed before that failure.

#### Scenario: Budget exhaustion is observable

- **GIVEN** eligible transcript operations remain after a cleanup pass reaches
  its fixed budget
- **WHEN** the pass stops
- **THEN** aggregate telemetry reports the deleted count and a budget-exhausted
  outcome
- **AND** reports that backlog is likely to remain

#### Scenario: A drained pass clears the backlog signal

- **WHEN** a cleanup pass selects fewer operations than the repository batch
  size, even if ownership rechecks deleted fewer rows from an earlier full
  selection
- **THEN** aggregate telemetry reports a completed outcome
- **AND** clears the backlog-likely signal

#### Scenario: Failed pass preserves partial progress telemetry

- **GIVEN** one or more cleanup batches commit before a later batch fails
- **WHEN** failure telemetry is recorded
- **THEN** it includes the committed deletion and batch counts

#### Scenario: Cleanup telemetry contains no sensitive labels

- **WHEN** cleanup succeeds or fails
- **THEN** its logs and metric labels contain no operation, session, account,
  prompt, output, or model values
