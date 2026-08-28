## ADDED Requirements

### Requirement: Drain status exposes HTTP bridge activity

The internal `/internal/drain/status` payload MUST include bounded HTTP bridge
activity counters when the proxy service exposes bridge activity. The snapshot
MUST be non-blocking and MUST include whether HTTP bridge work is active, the
number of visible pending or queued bridge requests, the number of live bridge
sessions, the number of in-flight bridge session creations, the oldest in-flight
creation age in seconds, how many in-flight create markers are older than the
stale threshold, and how many completed in-flight create markers were cleaned
while building the snapshot. The HTTP bridge background cleanup task count MUST
include only active HTTP bridge close/cleanup tasks, not unrelated work stored in
shared background task registries.

#### Scenario: Drain status reports bridge work

- **WHEN** `/internal/drain/status` is requested while the proxy service has
  HTTP bridge sessions, queued work, or in-flight bridge session creation
- **THEN** the response includes HTTP bridge activity counters
- **AND** `http_bridge_active` is true when any pending, queued, session, or
  in-flight create count is non-zero

#### Scenario: Completed in-flight bridge creates are cleaned from drain status

- **WHEN** an in-flight HTTP bridge session creation marker is completed,
- **AND** `/internal/drain/status` builds the bridge activity snapshot
- **THEN** the completed marker is removed from the local in-flight create map
- **AND** the payload reports the cleaned marker count without
  blocking the health request

#### Scenario: Live stale-age bridge creates are reported but not expired

- **WHEN** an in-flight HTTP bridge session creation marker is older than the
  stale in-flight threshold but has not completed
- **AND** `/internal/drain/status` builds the bridge activity snapshot
- **THEN** the marker remains in the local in-flight create map
- **AND** the payload reports the stale marker count without completing the
  live session creation future
