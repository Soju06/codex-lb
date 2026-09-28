## ADDED Requirements

### Requirement: Background database tasks finish in-flight work before cancellation

When shutdown stops a background task that performs database work (the
leader-lease keeper and the periodic scheduler loops), it MUST first signal the
task to stop and then wait up to a fixed grace of 2 seconds for the task to
finish the unit of work in progress. It MUST cancel the task only if it is
still running after that grace, and MUST log a WARNING naming the task when it
does. It MUST then wait at most the same grace for the cancellation to take
effect. A task still running after that MUST be logged and tracked rather than
awaited indefinitely, and while any such task is still running the shutdown
MUST NOT be recorded as clean. Each of these waits MUST additionally be capped
by the time remaining in the shared drain deadline, and MUST NOT use the
post-drain cleanup reserve, which belongs to the steps that follow the scheduler
stops (leader-lease release, metrics-server wait, database disposal), so that
the sequential stops cannot push the process past its forced-exit deadline;
when no drain time remains the task MUST be cancelled without a grace wait. A task that is idle between ticks MUST exit on the stop
signal without waiting out the grace.

#### Scenario: Prompt shutdown after startup leaves no pool errors

- **GIVEN** a server on file-backed SQLite
- **WHEN** it receives SIGTERM immediately after its first successful `/health`
- **THEN** it logs no `Exception closing connection` or `Exception during reset` pool errors
- **AND** it exits within the shutdown budget without waiting out the lease-release deadline

#### Scenario: Leader lease is released on prompt shutdown

- **GIVEN** a single-instance server holding the scheduler leader lease
- **WHEN** it receives SIGTERM immediately after its first successful `/health`
- **THEN** no `scheduler_leader` row remains after exit

#### Scenario: A task busy past the grace is cancelled and named

- **GIVEN** a stopping task that is still busy 2 seconds after stop was requested
- **WHEN** the grace expires
- **THEN** the task is cancelled
- **AND** a WARNING log names the task

#### Scenario: A task deferring cancellation does not block shutdown

- **GIVEN** a cancelled task that is still running 2 seconds after cancellation
- **WHEN** the bounded wait expires
- **THEN** shutdown proceeds without awaiting it further
- **AND** a WARNING names the task
- **AND** the SQLite run-state is not recorded clean while it is still running

#### Scenario: An in-flight scheduler read completes during shutdown

- **GIVEN** the cache-invalidation poller is inside its database read when shutdown stops it
- **WHEN** the read finishes within the grace
- **THEN** the read completes instead of being cancelled

#### Scenario: Sequential stops stay within the shutdown budget

- **GIVEN** a committed shutdown with only a fraction of a second left in the shared drain deadline
- **WHEN** seven wedged background tasks are stopped one after another
- **THEN** all of them are stopped within that remaining budget, not 2 seconds (or more) each
- **AND** a task that defers cancellation is still tracked so the shutdown is not recorded clean

#### Scenario: Idle tasks stop immediately

- **GIVEN** a scheduler loop waiting on its stop event between ticks
- **WHEN** stop is requested
- **THEN** the loop exits without waiting out the grace
