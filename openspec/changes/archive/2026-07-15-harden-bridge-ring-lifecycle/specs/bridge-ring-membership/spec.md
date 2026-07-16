# bridge-ring-membership Delta

## ADDED Requirements

### Requirement: Replicas register in the bridge ring before serving bridge traffic
Each replica MUST register its instance id (and advertised endpoint when configured) in the shared `bridge_ring_members` table before hard-affinity HTTP bridge requests are admitted. While registration is incomplete in a multi-replica deployment, hard-affinity bridge requests MUST wait for registration up to the configured connect timeout and fail with a retryable `bridge_owner_unreachable` error when the wait expires.

#### Scenario: Hard-affinity request before registration completes
- **WHEN** a hard-affinity HTTP bridge request arrives on a replica whose ring registration has not completed
- **AND** the deployment requires cluster registration (multi-instance ring or non-loopback advertise URL)
- **THEN** the request waits for registration up to the configured connect timeout
- **AND** it fails with a retryable `bridge_owner_unreachable` error if registration is still incomplete

### Requirement: Ring membership is maintained by periodic heartbeats
Each registered replica MUST refresh its ring row via an upsert heartbeat every 10 seconds so sibling replicas observing the shared table converge on the same active-member view. Ring readers MUST treat a member as active only when its heartbeat is within the 30-second stale threshold.

#### Scenario: Missed heartbeats age a member out of the active ring
- **WHEN** a replica stops heartbeating for longer than the stale threshold
- **THEN** ring readers no longer include that instance in the active member list
- **AND** owner-endpoint resolution for that instance returns no endpoint

#### Scenario: Heartbeat recovers a row removed by a sibling
- **WHEN** a replica's ring row was deleted or aged by another process
- **THEN** the replica's next heartbeat re-upserts the row with a fresh timestamp

### Requirement: Shutdown ages the ring row instead of deleting it
On graceful shutdown a replica MUST age its ring row's heartbeat close to the stale threshold rather than deleting the row, leaving a short grace window (heartbeat interval plus 5 seconds) during which sibling workers sharing the same instance id can refresh the row while a fully terminating pod still ages out quickly.

#### Scenario: Terminating pod leaves a short grace window
- **WHEN** a replica shuts down gracefully
- **THEN** its ring row's heartbeat is set so the member ages out after the shutdown grace window
- **AND** the row is not deleted, so a surviving sibling worker's next heartbeat can restore it

### Requirement: Dead ring rows are purged
The background cleanup loop MUST delete `bridge_ring_members` rows whose heartbeat is older than 24 hours so rows for permanently departed replicas do not accumulate. Rows within the retention window MUST NOT be deleted so shutdown stale-aging and restart recovery keep working.

#### Scenario: Cleanup removes long-dead members and keeps recent ones
- **WHEN** the cleanup loop runs
- **AND** one ring row's heartbeat is older than 24 hours while another's is recent
- **THEN** the old row is deleted
- **AND** the recent row is preserved
