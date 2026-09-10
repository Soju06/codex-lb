## ADDED Requirements

### Requirement: Direct WebSocket dispatch honors shared account unavailability

For every direct Responses WebSocket response.create, the proxy MUST check the shared routing availability snapshot before reusing an open upstream socket and immediately before dispatch after admission waits. Once that snapshot marks the account paused, requiring reauthentication, deactivated, or deleted, the proxy MUST NOT send a new response.create through that account. An idle unavailable socket MUST be retired through normal cleanup and selection; movable requests MUST remain eligible for an active replacement, while account-owned payloads and hard continuity MUST retain their owner and fail closed if it is unavailable. The check MUST NOT re-run selection for a still-available ordinary socket solely to check account status.

If another accepted request owns the socket, the proxy MUST reject only the unsent turn without retiring the socket or penalizing account health. A pause observed after admission MUST reject that turn before send and release its API-key reservation, work admission, response-create gate, and account create lease. Accepted work MUST remain eligible to finish. Local rejection MUST be recorded in request logs without an upstream health penalty. The unsent request MUST retain a scope cleanup owner until rejection finishes, including cancellation during logging or terminal delivery.

#### Scenario: Paused idle socket cannot serve a fresh turn

- **GIVEN** an ordinary WebSocket completed a response on account A
- **AND** the shared availability snapshot observes an operator pause of A
- **WHEN** the client sends a movable fresh response.create
- **THEN** the old socket is retired and normal selection can connect to active account B
- **AND** no second response.create is sent through A

#### Scenario: Paused response owner cannot be bypassed

- **GIVEN** the existing socket belongs to paused account A
- **WHEN** a follow-up is pinned to A by previous_response_id or an account-scoped file
- **THEN** ordinary owner selection fails closed without sending through A or transferring the payload to B

#### Scenario: Pause does not cancel an accepted sibling

- **GIVEN** an accepted response is still streaming on A when the pause is observed
- **WHEN** a second response.create arrives
- **THEN** only the unsent turn is rejected and settled
- **AND** the accepted response can complete on the original socket

#### Scenario: Pause wins during admission

- **GIVEN** a request is waiting for response-create admission on an available account
- **WHEN** the shared availability snapshot observes a pause before the wait returns
- **THEN** the final dispatch check rejects it without sending upstream
- **AND** all request-owned reservations and admission leases are released

#### Scenario: Available ordinary owner keeps its fast path

- **WHEN** a follow-up reuses a socket whose account remains available
- **THEN** the availability check requires no additional account selection or database status query

#### Scenario: Cancellation during rejection preserves cleanup

- **WHEN** local rejection is cancelled while logging or delivering its terminal error
- **THEN** scope finalization still owns and settles the unsent request
- **AND** admission and create leases are released without sending it upstream or penalizing account health
