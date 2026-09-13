# live-usage-ingestion Delta

## ADDED Requirements

### Requirement: Ingestor-owned task failures are settled at completion

Every background task the live usage ingestor creates MUST be settled when it
completes: if the task ends with an exception other than cancellation, the
exception MUST be retrieved at completion time, logged immediately with its
traceback, and recorded in a bounded in-process failure record as
traceback-free metadata (task name and exception representation) so the
record cannot retain the failed task's object graph. An
ingestor-owned task failure MUST NOT surface as a garbage-collection-time
unobserved-task warning. Each task MUST be settled exactly once, including
when an external supervisor (such as test infrastructure) also observes the
task. Settlement MUST NOT extend task lifetime, change ingestion behavior, or
affect the serving path.

#### Scenario: Detached consumer death is logged deterministically

- **GIVEN** a consumer task whose owner lost track of it (for example a stop
  cancelled between clearing the singleton and awaiting the task)
- **WHEN** the task dies with an exception
- **THEN** the exception is retrieved and logged at completion time
- **AND** it is recorded in the bounded failure record
- **AND** no unobserved-task warning fires at garbage collection

#### Scenario: Cancelled tasks settle silently

- **WHEN** an ingestor-owned task ends by cancellation
- **THEN** settlement records no failure and logs no error

#### Scenario: Failure record stays bounded

- **WHEN** ingestor-owned tasks fail repeatedly without the record being
  drained
- **THEN** the failure record retains at most its fixed capacity of entries
- **AND** every failure is still logged

### Requirement: Ingestor lifecycle is instance-scoped

Each application lifespan MUST hold the ingestor instance its startup created
and stop exactly that instance at shutdown. Stopping an instance MUST touch
the process-wide singleton registration and the publisher hook only when the
stopped instance still owns them; when it does own them, the most recently
displaced ingestor that is still running MUST be restored as the registration
and publisher. Restoration eligibility is defined as: the candidate holds an
existing consumer task whose `done()` is false — this excludes consumers that
failed or completed, and ingestors whose `stop()` already cleared their
consumer. An instance MUST be removed from restoration tracking before its own
shutdown begins, so a stopping or stopped instance can never be restored
later. When several
lifespans are live in one process, no lifespan's startup or shutdown may
orphan another lifespan's running ingestor, leave it without a stop path, or
leave it registered-less while it still runs.

#### Scenario: Nested lifespan cannot orphan the outer ingestor

- **GIVEN** an app whose lifespan started ingestor A
- **WHEN** a nested lifespan starts ingestor B (taking over the singleton and
  publisher) and later stops it
- **THEN** ingestor A keeps running, strongly rooted by its own lifespan
- **AND** the outer lifespan's shutdown stops ingestor A and its tasks

#### Scenario: Nested shutdown restores the outer registration

- **GIVEN** an app whose lifespan started ingestor A
- **AND** a nested lifespan whose startup displaced A by registering
  ingestor B
- **WHEN** the nested lifespan stops ingestor B
- **THEN** ingestor A is restored as the singleton registration and publisher
- **AND** publications after the nested exit flow to ingestor A and are
  ingested
- **AND** a displaced ingestor that already stopped is not restored (the
  registration falls through to the next still-running displaced instance,
  or is cleared)

#### Scenario: A failed displaced ingestor is not restored

- **GIVEN** displaced ingestor A whose consumer task has settled with an
  exception (its task `done()` is true)
- **WHEN** the current registration stops
- **THEN** A is skipped by restoration (fall through to the next eligible
  displaced instance, or clear the registration)

#### Scenario: A stopping displaced ingestor is not restored

- **GIVEN** displaced ingestor A whose `stop()` has begun (A was removed from
  restoration tracking before its shutdown started)
- **WHEN** the current registration stops concurrently
- **THEN** A is never restored, even if its consumer task has not yet finished
