## ADDED Requirements

### Requirement: Pre-created WebSocket model rejection retries only movable requests

A Responses WebSocket request MUST enter the existing bounded replay path only
when it has not received `response.created`, has no downstream-visible output
or accepted response identifier, has no other pending request, and receives a
terminal `error` or `response.failed` event with exact code `model_not_found`.
The replay MAY select another eligible account only when the request is
movable. A
required previous-response owner, turn-state owner, or file owner MUST retain
that owner: a connect-phase `model_not_found` from it MUST surface the original
upstream status and envelope, and MUST NOT exclude the owner or turn into
`previous_response_owner_unavailable`. After acceptance or visible output,
the existing fail-closed replay rules remain authoritative.

#### Scenario: Movable pre-created model rejection retries an eligible account

- **GIVEN** a movable Responses WebSocket request has been sent but has not
  received `response.created`
- **AND** account A emits an `error` frame with code `model_not_found`
- **WHEN** account B is eligible
- **THEN** the proxy retries once through the existing bounded path on B
- **AND** it does not emit the rejection from A to the client

#### Scenario: Required owner surfaces its own model rejection

- **GIVEN** a WebSocket connect is required to use previous-response owner A
- **AND** A returns HTTP 404 with code `model_not_found`
- **WHEN** the connect failure is handled
- **THEN** the client receives A's original 404 model rejection
- **AND** no account B selection is attempted
