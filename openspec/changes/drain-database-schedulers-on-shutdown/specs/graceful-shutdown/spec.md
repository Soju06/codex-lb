## ADDED Requirements

### Requirement: Background database tasks finish in-flight work before cancellation

When shutdown stops a background task that performs database work (the
leader-lease keeper and the periodic scheduler loops), it MUST first signal the
task to stop and then wait up to a fixed grace of 2 seconds for the task to
finish the unit of work in progress. It MUST cancel the task only if it is
still running after that grace, and MUST log a WARNING naming the task when it
does. A task that is idle between ticks MUST exit on the stop signal without
waiting out the grace.

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

#### Scenario: Idle tasks stop immediately

- **GIVEN** a scheduler loop waiting on its stop event between ticks
- **WHEN** stop is requested
- **THEN** the loop exits without waiting out the grace
