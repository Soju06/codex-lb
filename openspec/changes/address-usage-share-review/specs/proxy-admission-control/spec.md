## ADDED Requirements

### Requirement: Direct WebSocket reservation refusals finalize prepared turns

When API-key fixed-limit reservation raises a domain error after a direct Responses WebSocket turn has been prepared but before it is registered for upstream dispatch, the proxy MUST invoke the existing request-state reservation cleanup before recording exactly one terminal error log for that turn. The log MUST retain the prepared request's identity and API-key attribution and MUST NOT attribute the refusal to a subscription account. The proxy MUST preserve the original domain error's client-visible status, code, and message, MUST NOT send the refused turn upstream, and MUST NOT close or penalize a healthy existing subscription socket because of that local refusal.

#### Scenario: Exhausted fixed limit refuses the first prepared turn

- **GIVEN** a Responses WebSocket receives its first turn with an exhausted API-key fixed limit
- **WHEN** reservation rejects the prepared turn
- **THEN** request-state cleanup precedes its single account-neutral error log
- **AND** the client receives the existing `429 rate_limit_exceeded` error without an upstream connection or orphaned reservation

#### Scenario: Reservation rejects a fresh turn on a reusable socket

- **GIVEN** a direct Responses WebSocket already has a healthy subscription upstream
- **WHEN** reservation rejects a later prepared turn because its fixed limit is exhausted or its key became invalid after policy refresh
- **THEN** the turn is cleaned up and logged once without account attribution
- **AND** its original `429 rate_limit_exceeded` or `401 invalid_api_key` error is preserved
- **AND** the upstream receives no refused frame and remains available for a subsequent eligible turn

#### Scenario: Successful preparation keeps reservation ownership

- **GIVEN** a prepared turn whose API-key fixed-limit reservation succeeds
- **WHEN** the preparation helper returns
- **THEN** the returned request state owns its reservation until the existing dispatch or terminal cleanup settles it
- **AND** no reservation-refusal log is recorded
