## ADDED Requirements

### Requirement: Background database tasks finish in-flight work before cancellation

When shutdown stops a background task that performs database work (the
leader-lease keeper and the periodic scheduler loops), it MUST first signal the
task to stop and then wait up to a fixed grace of 2 seconds for the task to
finish the unit of work in progress. It MUST cancel the task only if it is
still running after that grace, and MUST log a WARNING naming the task when it
does. Before any grace wait the stop MUST yield one event-loop turn, and a task
that has finished by then (a loop idling on its stop event) MUST NOT be warned
about or cancelled.

For the periodic scheduler loops, the grace MUST be capped by the time remaining
in the shared drain deadline and MUST NOT use the post-drain cleanup reserve,
which belongs to the steps that follow the stops (leader-lease release,
metrics-server wait, database disposal). After cancelling, the stop MUST wait
up to the plain grace for the cancellation to take effect, so that a promptly
cancelled task has finished before the next stop in the shutdown order begins.
A task still running after that MUST be logged and tracked, and while any such
task is still running the shutdown MUST NOT be recorded as clean.

For the leader-lease keeper, which is stopped inside `release()` under the
release's own bounded deadline, the stop MUST use the plain grace (not the
drain-capped one) and, after cancelling, MUST wait until the keeper has
finished, so exactly one owner renews the lease at a time.

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

#### Scenario: Idle loop does not warn when no drain time is left

- **GIVEN** the shared drain deadline is exhausted
- **WHEN** a scheduler loop idling on its stop event is stopped
- **THEN** it exits without a WARNING and without being cancelled

#### Scenario: Keeper finishes before release continues when no drain time is left

- **GIVEN** the shared drain deadline is exhausted and the keeper is inside a lease renewal whose session close defers cancellation
- **WHEN** `release()` stops the keeper
- **THEN** the keeper's session has closed before `release()` drains detached bodies and deletes the lease

#### Scenario: Idle tasks stop immediately

- **GIVEN** a scheduler loop waiting on its stop event between ticks
- **WHEN** stop is requested
- **THEN** the loop exits without waiting out the grace
