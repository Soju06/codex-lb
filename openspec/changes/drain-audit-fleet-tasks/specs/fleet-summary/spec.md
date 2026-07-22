## ADDED Requirements

### Requirement: Detached fleet refreshes participate in graceful shutdown

When a `POST /api/fleet/refresh` caller is cancelled after its refresh has started, the system MUST keep the refresh strongly owned so its dedicated session can finish and be closed. Graceful shutdown MUST wait for all such tracked refreshes for up to `shutdown_drain_timeout_seconds` before stopping usage-refresh singleflight work or closing shared HTTP and database resources. If the deadline expires, the system MUST report each fleet refresh that did not drain before continuing shutdown.

#### Scenario: Caller cancellation does not orphan fleet refresh work

- **GIVEN** a fleet refresh is running in its dedicated session
- **WHEN** the requesting client disconnects or its request task is cancelled
- **THEN** the refresh continues independently of the cancelled caller
- **AND** it remains tracked until its session exits

#### Scenario: Shutdown waits for a detached fleet refresh

- **GIVEN** a cancelled-request fleet refresh is still pending when graceful shutdown begins
- **WHEN** the refresh completes within the configured drain timeout
- **THEN** shutdown waits for the refresh
- **AND** usage singleflight, shared HTTP clients, and database engines remain available until it finishes

#### Scenario: Overdue fleet refresh is reported

- **GIVEN** a detached fleet refresh remains pending for the full configured drain timeout
- **WHEN** graceful shutdown drains fleet tasks
- **THEN** the drain reports that task as overdue
- **AND** shutdown is allowed to continue
