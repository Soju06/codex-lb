## ADDED Requirements

### Requirement: Pre-created WebSocket model rejection retries only movable requests

A Responses WebSocket request MUST enter the existing bounded replay path only
when it has not received `response.created`, has no downstream-visible output
or accepted response identifier, has no other pending request, and receives a
terminal `error` or `response.failed` event with exact code `model_not_found`.
The replay MAY select another eligible account only when the request is
movable. Whether the request is movable MUST be decided after the replay body
has been prepared, against the owner pins that body still carries: an owner
the proxy is not entitled to release (a client-supplied `previous_response_id`,
an input-file owner, or a turn-state owner) MUST retain that owner and receive
the original rejection without any replacement selection, while a
proxy-injected continuity anchor whose retained fresh body is self-contained
and account-neutral MUST be released together with its owner pin so the
replay may move. The legacy entitlement rejection
(`account_model_unsupported`) and exact `model_not_found` MUST take the same
replay path, so a continuation turn keeps the failover it has on a first turn.
A temporary forced-refresh preference alone MUST NOT make a request
owner-bound. A connect-phase
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

#### Scenario: Releasable continuity anchor still moves the follow-up turn

- **GIVEN** a Codex WebSocket session completed its first turn on account A
- **AND** the follow-up repeats the history, so the proxy anchors it on the
  completed response and retains the self-contained full resend
- **WHEN** A emits a pre-created `error` frame with the legacy entitlement
  message or with code `model_not_found`
- **THEN** the proxy installs the retained full resend, releases the anchor's
  owner pin, excludes A and replays once on eligible account B
- **AND** the client observes only B's response lifecycle

#### Scenario: Turn-state owner keeps its own pre-created model rejection

- **GIVEN** a native turn-state session whose follow-up is owner-bound to
  account A by the turn state
- **WHEN** A emits a pre-created `error` frame with the legacy entitlement
  message or with code `model_not_found`
- **THEN** the client receives A's original rejection
- **AND** A is not excluded and no replacement connect is attempted

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
