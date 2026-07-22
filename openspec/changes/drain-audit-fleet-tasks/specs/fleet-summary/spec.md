## ADDED Requirements

### Requirement: Fleet refreshes participate in graceful shutdown

The system MUST strongly own every accepted `POST /api/fleet/refresh` task from creation until its dedicated session has finished and closed, regardless of whether its caller remains attached. Task creation and registry insertion MUST occur synchronously before the route first awaits the task. Graceful shutdown MUST wait for all such tracked refreshes for up to `shutdown_drain_timeout_seconds` before stopping usage-refresh singleflight work or closing shared HTTP and database resources. If the deadline expires, the system MUST report each fleet refresh that did not drain before continuing shutdown.

#### Scenario: Caller cancellation does not orphan fleet refresh work

- **GIVEN** a fleet refresh is running in its dedicated session
- **WHEN** the requesting client disconnects or its request task is cancelled
- **THEN** the refresh continues independently of the cancelled caller
- **AND** it remains tracked until its session exits

#### Scenario: Shutdown begins before caller cancellation

- **GIVEN** a fleet refresh was accepted and its caller remains attached
- **WHEN** the in-flight drain times out and graceful shutdown starts draining fleet tasks
- **THEN** the refresh is already present in the fleet task registry
- **AND** cancelling the caller afterward does not remove the refresh from shutdown ownership

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

### Requirement: Post-cutoff fleet refreshes are rejected before resource work

Immediately after the in-flight drain attempt returns, graceful shutdown MUST synchronously close fleet task admission before any further shutdown await. A `POST /api/fleet/refresh` request that reaches its producer after this cutoff MUST return the dashboard `503 service_unavailable` error envelope and MUST NOT create a refresh coroutine, task, background session, or other refresh resource work.

#### Scenario: Late fleet producer receives service unavailable

- **GIVEN** graceful shutdown has closed control-plane task admission
- **WHEN** an authenticated caller requests `POST /api/fleet/refresh`
- **THEN** the response status is 503
- **AND** the dashboard error code is `service_unavailable`
- **AND** no fleet refresh task or background session starts
