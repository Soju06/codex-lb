## MODIFIED Requirements

### Requirement: Per-request detached-session retire sweep bounds its lock wait

The fail-safe sweep that reconsiders detached HTTP-bridge generations on every bridge request MUST use one five-second monotonic deadline for its aggregate detached-session lock waits. Each retirement attempt MUST receive only the remaining time. Once the deadline expires, the sweep MUST stop starting attempts and emit one warning naming the number of unattempted sessions, if any. Timed-out and unattempted sessions MUST remain tracked with their lock state untouched for later sweeps and lifecycle cleanup. Session lifecycle owners (drain, close, cooldown-suppression retirement) MUST keep waiting for the lock without a bound so retirement decisions stay authoritative. Request cancellation MUST NOT bypass the shielded finalization sweep or transfer resource cleanup ownership.

#### Scenario: Busy detached lock does not park the request path

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is held by another task for longer than the bound
- **WHEN** a request runs the fail-safe sweep
- **THEN** the sweep returns after the bound without closing the session
- **AND** if sessions remain unattempted when the deadline expires, one warning reports their count
- **AND** the timed-out session remains tracked for later sweeps and lifecycle cleanup
- **AND** the lock remains owned by its holder with no stranded waiter

#### Scenario: Free detached lock still retires

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is free and which no turn owns
- **WHEN** a request runs the fail-safe sweep
- **THEN** the session is retired exactly as before

#### Scenario: Lifecycle owners keep the unbounded wait

- **GIVEN** a drain or close path calls the retire check without a bound while another task briefly holds the lock
- **WHEN** the holder releases
- **THEN** the retire check proceeds and retires the session

#### Scenario: Several busy sessions share one deadline

- **GIVEN** three detached sessions and a five-second sweep budget
- **WHEN** the first retirement attempt consumes three seconds and the second consumes its remaining two seconds
- **THEN** no third attempt starts and aggregate lock waiting is five seconds
- **AND** the deferred sessions remain tracked and a later sweep can retire them

#### Scenario: Cancelled request finalization uses the same deadline

- **WHEN** a bridge request is cancelled while several detached sessions have busy locks
- **THEN** shielded finalization uses one aggregate lock-wait deadline before cancellation propagates
- **AND** deferred sessions retain their existing cleanup owners
