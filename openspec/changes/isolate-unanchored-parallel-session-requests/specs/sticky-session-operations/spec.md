## ADDED Requirements

### Requirement: Unanchored process-session concurrency uses independent bridge lanes

When multiple Responses requests share a process-level session header but carry neither `previous_response_id` nor turn-state continuity, the service MUST NOT queue an independent request behind an active response-create gate. If the canonical bridge is still being created, reserved by another request before submit, already has a visible request, or belongs to a different model class, the service MUST create a server request-scoped bridge lane. The lane identity MUST NOT depend on a client-controlled request ID. The fork MUST leave the canonical bridge and its model metadata unchanged. Sequential idle requests of the same model class MAY keep reusing the canonical bridge. An aborted pre-submit lookup MUST NOT leave its canonical bridge reserved. Owner forwarding MUST preserve whether the originating request was unanchored instead of treating a proxy-generated downstream turn-state as an explicit client anchor. Durable aliases derived from the forked lane MUST retain hard owner and account continuity.

#### Scenario: Background requests do not block behind a foreground turn

- **GIVEN** a foreground request is active on a session-header bridge
- **WHEN** two unanchored background requests arrive with the same session header
- **THEN** each background request uses an independent response-create gate
- **AND** neither request waits for the foreground response to complete
- **AND** the foreground bridge's model metadata remains unchanged

#### Scenario: Lookup-to-submit requests remain isolated

- **GIVEN** an unanchored request has reserved an idle canonical bridge but has not yet made queued activity visible
- **WHEN** another unanchored request arrives with the same session header and client request ID
- **THEN** the second request uses a distinct server-scoped bridge lane
- **AND** it does not reuse the reserved canonical bridge

#### Scenario: Cancelled lookup does not strand a reservation

- **GIVEN** an unanchored request is reusing an idle canonical bridge
- **WHEN** the request is cancelled during durable-session refresh before submit
- **THEN** the canonical bridge remains unreserved
- **AND** later requests are not forced onto fork lanes by the cancelled lookup

#### Scenario: Remote owner preserves unanchored concurrency

- **GIVEN** an unanchored request is forwarded to the canonical bridge owner
- **AND** the proxy generated a downstream turn-state for response aliasing
- **WHEN** the owner receives the forwarded request while the canonical lane is active
- **THEN** the owner still treats the request as unanchored
- **AND** the request uses an independent bridge lane

#### Scenario: Durable fork continuation remains owner-bound

- **GIVEN** a forked lane has produced a durable turn-state or previous-response alias
- **WHEN** a later request resolves that alias on another instance
- **THEN** the request follows the hard owner-bound continuity path
- **AND** the original account binding is preserved

#### Scenario: Explicit continuation is not split

- **WHEN** a request carries `previous_response_id` or a turn-state header
- **THEN** the service keeps the request on the hard owner-bound continuity path
- **AND** it does not apply unanchored parallel-session isolation
