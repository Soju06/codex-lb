## ADDED Requirements

### Requirement: HTTP bridge admission waiters survive upstream replacement

The proxy MUST preserve an HTTP bridge session when its upstream connection
terminates while an unsent request is already waiting for that session's
response-create admission. It MUST fail the requests that were pending on the
terminated upstream but MUST NOT retire, unregister, prune, or release the
retained session while the unsent waiter owns the handoff.

After the waiter acquires admission, the proxy MUST reconnect the retained
session before sending the request. A waiter that has not entered the pending
request queue and has no upstream send timestamp MAY be sent exactly once on
that fresh connection. Hard-affinity sessions MUST retain their account and
continuity ownership during this handoff. If the session was replaced or
unregistered, or reconnection fails, the proxy MUST fail closed without sending
the waiter. Cancelling or failing the last waiter MUST allow the closed session
to retire and release its resources.

#### Scenario: admitted follow-up survives an upstream close

- **GIVEN** one HTTP bridge request is pending upstream
- **AND** a follow-up request is unsent and waiting on the same response-create gate
- **WHEN** the upstream connection closes before the follow-up acquires the gate
- **THEN** the pending request receives its terminal continuity failure
- **AND** the session remains registered and protected from pruning for the waiter
- **AND** the waiter reconnects the retained session and is sent exactly once
- **AND** the waiter does not receive an internal bridge-closed error

#### Scenario: unsafe handoff fails closed

- **GIVEN** an unsent waiter whose prior session was replaced or unregistered
- **OR** the retained session cannot reconnect
- **WHEN** the waiter acquires admission
- **THEN** the waiter is not sent
- **AND** the request receives an explicit retryable proxy error
