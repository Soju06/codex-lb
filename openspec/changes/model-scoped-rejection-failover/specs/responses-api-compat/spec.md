## ADDED Requirements

### Requirement: Pre-created WebSocket model rejection retries only movable requests

A Responses WebSocket request MUST enter the existing bounded replay path only
when it has not received `response.created`, has no downstream-visible output
or accepted response identifier, has no other pending request, and receives a
terminal `error` or `response.failed` event with exact code `model_not_found`.
The replay MAY select another eligible account only when the request is
movable. A required previous-response owner, turn-state owner, replay-required
owner, or file owner MUST retain that owner; a temporary forced-refresh
preference alone MUST NOT make a request owner-bound. A connect-phase
`model_not_found` from a required owner MUST surface the original upstream
status and envelope, and MUST NOT exclude the owner or turn into
`previous_response_owner_unavailable`. If bounded replacement selection for a
movable request is exhausted, the proxy MUST surface the rejected account's
original status, code, message, type, and parameter instead of a generated
no-account error. After acceptance or visible output, the existing fail-closed
replay rules remain authoritative.

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

#### Scenario: Temporary refresh preference remains movable

- **GIVEN** a movable request retained a preferred account only for forced
  refresh and that refresh completed
- **WHEN** the account emits a pre-created `model_not_found`
- **THEN** the request follows the bounded movable replay path rather than
  treating the temporary preference as an owner pin

#### Scenario: Exhausted movable rejection retains its envelope

- **GIVEN** a movable pre-created request receives HTTP 404
  `model_not_found` from its only eligible account
- **WHEN** bounded replacement selection cannot select another account
- **THEN** the client receives that original HTTP 404, error code, message,
  type, and parameter
