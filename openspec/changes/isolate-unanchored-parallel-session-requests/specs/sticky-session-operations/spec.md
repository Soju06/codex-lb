## ADDED Requirements

### Requirement: Unanchored process-session concurrency uses independent bridge lanes

When multiple Responses requests share a process-level session header but carry neither `previous_response_id` nor turn-state continuity, the service MUST NOT queue an independent request behind an active response-create gate. If the canonical bridge is still being created, already has a visible request, or belongs to a different model class, the service MUST create a request-scoped soft bridge lane. The fork MUST leave the canonical bridge and its model metadata unchanged. Sequential idle requests of the same model class MAY keep reusing the canonical bridge.

#### Scenario: Background requests do not block behind a foreground turn

- **GIVEN** a foreground request is active on a session-header bridge
- **WHEN** two unanchored background requests arrive with the same session header
- **THEN** each background request uses an independent response-create gate
- **AND** neither request waits for the foreground response to complete
- **AND** the foreground bridge's model metadata remains unchanged

#### Scenario: Explicit continuation is not split

- **WHEN** a request carries `previous_response_id` or a turn-state header
- **THEN** the service keeps the request on the hard owner-bound continuity path
- **AND** it does not apply unanchored parallel-session isolation
