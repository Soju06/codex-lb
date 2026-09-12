## MODIFIED Requirements

### Requirement: Ring membership is maintained by periodic heartbeats
Each registered replica MUST refresh its ring row via an upsert heartbeat every 10 seconds so sibling replicas observing the shared table converge on the same active-member view. Ring renewal MUST run under an independently supervised periodic owner, MUST use local database-pool admission capacity isolated from durable-ownership reconciliation and cap-partition refresh, and MUST NOT await durable-ownership reconciliation, idle-session sweeping, cap-partition refresh, or other optional maintenance. Each heartbeat attempt MUST have a bounded execution deadline, MUST have at most one in-flight owner, and a failed or timed-out attempt MUST NOT terminate future heartbeat scheduling. If the periodic worker exits unexpectedly while the lifespan is active, supervision MUST record the exit and restart the worker with bounded delay. Ring readers MUST treat a member as active only when its heartbeat is within the 30-second stale threshold.

#### Scenario: Missed heartbeats age a member out of the active ring
- **WHEN** a replica stops heartbeating for longer than the stale threshold
- **THEN** ring readers no longer include that instance in the active member list
- **AND** owner-endpoint resolution for that instance returns no endpoint

#### Scenario: Heartbeat recovers a row removed by a sibling
- **WHEN** a replica's ring row was deleted or aged by another process
- **THEN** the replica's next heartbeat re-upserts the row with a fresh timestamp

#### Scenario: Blocked maintenance does not delay renewal
- **GIVEN** durable-ownership reconciliation, idle-session sweeping, or cap-partition refresh remains blocked longer than a heartbeat interval
- **WHEN** the independent heartbeat cadence becomes due
- **THEN** the replica still attempts its ring heartbeat without waiting for that maintenance phase
- **AND** no second owner is created for the still-running maintenance phase

#### Scenario: Optional maintenance occupies the request database pool
- **GIVEN** durable-ownership reconciliation and cap-partition refresh occupy the request database pool's available connections
- **WHEN** the independent heartbeat cadence becomes due
- **THEN** heartbeat renewal uses its isolated local pool admission path
- **AND** attempts the ring upsert without waiting for those optional phases to release request-pool connections

#### Scenario: Timed-out heartbeat attempt does not overlap its owner
- **GIVEN** a heartbeat attempt exceeds its execution deadline and remains unfinished while cancellation drains
- **WHEN** another heartbeat interval becomes due
- **THEN** the runtime does not start a concurrent heartbeat against the same replica row
- **AND** readiness ages the existing row out normally if no attempt completes successfully

#### Scenario: Unexpected heartbeat worker exit is supervised
- **WHEN** the heartbeat worker exits unexpectedly while the application lifespan is still active
- **THEN** the runtime records the failure
- **AND** restarts heartbeat scheduling after a bounded delay

#### Scenario: Shutdown owns every periodic worker
- **WHEN** graceful shutdown begins
- **THEN** the runtime cancels and drains the heartbeat and maintenance owners within the existing shutdown bounds
- **AND** it stops registration and heartbeat renewal before aging the shared ring row for shutdown
- **AND** periodic drainage and stale-marking consume one shared absolute shutdown deadline without starting an additive timeout
- **AND** a remaining maintenance owner MUST NOT prevent stale-marking once both ring writers have stopped and shared deadline time remains
- **AND** a stale-mark operation that exceeds the deadline remains strongly owned, receives cancellation, and suppresses the SQLite clean-shutdown marker
- **AND** if any registration or periodic owner remains active after the bound, the runtime withholds the SQLite clean-shutdown marker

## ADDED Requirements

### Requirement: Heartbeat diagnostics preserve readiness policy
When the HTTP Responses session bridge is enabled and bridge registration has completed, `/health/ready` MUST preserve the empty-active-ring exemption. Absence from a nonempty active ring MUST remain unready. Liveness MUST remain independent of ring membership. When the bridge is disabled, a non-draining replica whose database probe succeeds MUST return HTTP 200 from `/health/ready` regardless of bridge schema readiness, registration state, ring lookup errors, or ring membership.

#### Scenario: Single replica ages out of an empty ring
- **GIVEN** bridge registration completed for the only replica
- **AND** its heartbeat is older than the stale threshold so the active ring is empty
- **WHEN** `/health/ready` probes that replica
- **THEN** ring membership does not make the readiness probe fail
- **AND** the response reports the stale heartbeat age and inactive local membership

#### Scenario: Active local member remains ready
- **GIVEN** bridge registration completed and the probed replica has a fresh ring heartbeat
- **WHEN** `/health/ready` checks infrastructure readiness
- **THEN** ring membership does not make the probe fail

#### Scenario: Bridge-disabled readiness ignores ring state
- **GIVEN** the HTTP Responses session bridge is disabled
- **AND** the replica is not draining and its database probe succeeds
- **WHEN** `/health/ready` is requested
- **THEN** the probe returns HTTP 200 with status `ok`
- **AND** bridge schema readiness, registration state, ring lookup errors, and ring membership do not make the probe fail

#### Scenario: Liveness ignores stale membership
- **GIVEN** the probed replica is absent from the active ring
- **WHEN** `/health/live` is requested
- **THEN** liveness remains successful
