## ADDED Requirements

### Requirement: Refresh headroom before analytical expiry
After successfully publishing an analytical snapshot, the producer SHALL schedule
the next collection before the configured refresh-interval boundary by the existing
collector timeout plus two scheduler ticks, with a minimum one-second delay.
This scheduling change SHALL NOT change the configured interval, the reader's
existing freshness/grace policy, or generation and lease fencing.

#### Scenario: A normal refresh finishes before the prior value expires
- **WHEN** a five-minute snapshot is successfully published
- **THEN** its next collection is due after 200 seconds
- **AND** the previously published value remains readable during collection
- **AND** successful replacement updates its generation and generation timestamp

#### Scenario: A short refresh interval remains bounded
- **WHEN** the interval is no longer than the refresh headroom
- **THEN** the next collection is scheduled at least one second after publication

#### Scenario: Failed work cannot make an old value fresh
- **WHEN** a replacement fails or its producer stops
- **THEN** the prior generation remains readable with truthful freshness metadata
- **AND** the existing failure backoff and publication ownership fences remain in force


### Requirement: Smoke verification waits for prepared dashboard values
The isolated browser smoke backend SHALL retain local snapshot production while
disabling outbound background loops. It SHALL wait for analytical readiness before
launching the browser rather than treating infrastructure liveness as proof that
prepared dashboard values exist.

#### Scenario: A fresh isolated backend is still warming
- **WHEN** the backend serves liveness but has not published its initial snapshots
- **THEN** browser launch waits for analytical readiness
- **AND** the local producer remains able to publish those snapshots
