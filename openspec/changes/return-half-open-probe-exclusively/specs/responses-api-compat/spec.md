# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: HTTP bridge retry circuit MUST gate hard-key half-open probes

For hard HTTP bridge keys, the proxy MUST preserve one-probe exclusivity when a
server-side continuity failure returns an unused half-open retry-circuit probe.
The returned probe MUST become an elapsed cooldown that lets the
next reconnect acquire a fresh half-open lease; further concurrent reconnects
MUST remain suppressed until that probe settles the circuit. Returning a probe
MUST require ownership of the active half-open lease. A local stale-anchor reset
MUST disarm its pending response-create attempts and detach the session before
it returns the probe.

#### Scenario: Returned probe admits only one reconnect

- **GIVEN** a hard HTTP bridge key has an active half-open lease
- **WHEN** the admitted probe fails because the proxy lost continuity ownership
- **THEN** the proxy returns the unused probe as an elapsed cooldown
- **AND** the next reconnect acquires a fresh half-open lease
- **AND** another concurrent reconnect for the same hard key is suppressed

#### Scenario: Stale cleanup cannot release another probe

- **GIVEN** a hard HTTP bridge key has an active half-open lease owned by a session
- **WHEN** stale cleanup from another session with the same hard key tries to return the probe
- **THEN** the proxy keeps the active half-open lease intact

#### Scenario: Local stale-anchor reset disarms and detaches before releasing

- **GIVEN** a local stale-anchor reset is settling a hard HTTP bridge session
- **WHEN** the reset returns the half-open probe consumed by that request
- **THEN** pending response-create attempts on that session are already disarmed
- **AND** the session has already been detached from active bridge ownership
- **AND** reader teardown cannot charge the retry circuit for those attempts
