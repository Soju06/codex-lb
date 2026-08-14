## ADDED Requirements

### Requirement: Idle bridge sessions are swept without request traffic

The system MUST evict idle HTTP-bridge sessions on every replica independently of whether that replica is receiving bridge requests. The sweep MUST reuse the same eligibility the request path applies — a session with pending or queued work, an admission waiter, a handoff in progress, or an unanchored reservation, and a session still inside its idle TTL, MUST NOT be evicted — and MUST close evicted sessions through the existing bounded close path so a slow upstream-reader cancellation cannot block the caller. A sweep failure MUST NOT interrupt the loop that drives it, and MUST NOT prevent the other per-replica bridge upkeep that shares that loop from running.

#### Scenario: A replica with no bridge traffic still evicts idle sessions

- **GIVEN** a replica holds an idle bridge session past its idle TTL and receives no further bridge requests
- **WHEN** the sweep runs
- **THEN** the session is detached from the registry and closed, releasing its upstream WebSocket

#### Scenario: Sweep eligibility matches the request path

- **GIVEN** a session with pending work whose idle TTL has elapsed, and a session used moments ago
- **WHEN** the sweep runs
- **THEN** neither session is evicted

#### Scenario: One failing upkeep pass does not skip the other

- **GIVEN** the durable-ownership reconcile raises on a heartbeat tick
- **WHEN** that tick runs
- **THEN** the idle sweep still runs and the heartbeat loop continues

#### Scenario: Sweeping an empty registry does nothing

- **WHEN** the sweep runs with no registered bridge sessions
- **THEN** no session is closed and no cleanup work is scheduled
