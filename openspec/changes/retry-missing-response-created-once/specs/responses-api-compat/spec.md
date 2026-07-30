## MODIFIED Requirements

### Requirement: Failed precreated HTTP bridge replay retires stale sessions

When an HTTP bridge request is still pending before upstream
`response.completed` and the upstream websocket closes or times out before the
pending request can be completed, the service MUST fail the pending request
terminally and retire the affected bridge session if precreated replay does not
reconnect and resend successfully.

For an eventless `response.create` that reaches the owner-side
missing-`response.created` deadline, the service MUST cancel the old receive
wait and MAY transparently replay once only when the existing pre-created
replay predicate proves there is no matched response lifecycle, upstream model
output, downstream sequence, or downstream-visible output and the eventless
owner is the session's sole pending request. The service MUST process an
upstream receive result that completes while cancellation is attempted and
MUST NOT replay that accepted request. The replay MUST remain on the account
whose response-create concurrency lease the request holds and MUST preserve
hard-affinity and account-scoped file ownership. A request carrying
`previous_response_id` MUST fail closed unless the proxy retained an
explicitly retry-safe full-resend body that can be replayed without the anchor.

If that replay succeeds, the original downstream stream MUST continue without
a terminal event. If replay is ineligible, reconnect/resend fails, or the
replacement send also times out, the service MUST terminally settle the request
and retire the session exactly once without an account-health penalty for the
missing acknowledgement.

#### Scenario: First eventless timeout recovers transparently

- **GIVEN** an HTTP bridge request has no response lifecycle or visible progress
- **AND** its first `response.create` reaches the missing-created deadline
- **WHEN** the existing replay guards accept the request and reconnect/resend
  succeeds
- **THEN** the service continues reading the replacement upstream socket
- **AND** the downstream stream receives no terminal failure for the first
  timeout

#### Scenario: Response acknowledgement wins the cancellation race

- **GIVEN** an eventless HTTP bridge request reaches its first deadline
- **WHEN** the pending upstream receive completes as cancellation is attempted
- **THEN** the service processes that completed result through the normal event
  path
- **AND** it does not replay the request

#### Scenario: Continuity without a safe full resend is not replayed

- **GIVEN** an eventless HTTP bridge request carries `previous_response_id`
- **AND** the proxy has no explicitly retry-safe full-resend body
- **WHEN** the missing-created deadline elapses
- **THEN** the service does not replay the continuation
- **AND** it terminally settles the request and retires the bridge

#### Scenario: Replay preserves hard account and file ownership

- **GIVEN** an eventless request has hard affinity or an account-scoped file
- **WHEN** it is eligible for the one transparent replay
- **THEN** the replacement connection uses the required owner account
- **AND** the file reference is not moved to an account that does not own it

#### Scenario: Replay preserves the account concurrency lease

- **GIVEN** an eventless request holds an account-scoped response-create lease
- **WHEN** it is eligible for the one transparent replay
- **THEN** the replacement socket uses that same account
- **AND** the resend does not consume capacity on an unleased account

#### Scenario: Pending sibling blocks transparent replay

- **GIVEN** an eventless request shares its upstream socket with another pending
  request
- **WHEN** the missing-created deadline elapses
- **THEN** the service does not replace the shared socket for replay
- **AND** it terminally settles the stale bridge through existing cleanup

#### Scenario: Replacement timeout is terminal

- **GIVEN** an eventless request was replayed once on a fresh upstream socket
- **WHEN** the replacement send also misses `response.created`
- **THEN** the service does not replay again
- **AND** it emits one terminal failure and retires the bridge session

#### Scenario: Precreated replay fails after upstream disconnect

- **WHEN** an HTTP bridge request is pending before `response.completed`
- **AND** the upstream websocket closes before the request completes
- **AND** precreated replay fails to reconnect and resend the request
- **THEN** the pending request is removed from the bridge queue
- **AND** the per-session response-create gate is released
- **AND** the bridge session is closed and removed from local reuse
- **AND** the terminal error preserves the original failure code such as
  `stream_incomplete` or `upstream_request_timeout`

#### Scenario: Terminal logging failure does not preserve stale bridge ownership

- **WHEN** a failed pending HTTP bridge request is being logged as terminal
- **AND** request-log writing fails
- **THEN** the service still removes the stale bridge session from local reuse
- **AND** the service releases any durable bridge ownership for that stale
  session

#### Scenario: Concurrent waiter cannot submit on retired stale bridge

- **WHEN** an HTTP bridge request is waiting on a session response-create gate
- **AND** the upstream reader retires that same bridge session after a failed
  precreated replay
- **THEN** the waiting request or prewarm is rejected before it is appended to
  pending requests or sent upstream
- **AND** the retired bridge session remains closed and removed from local reuse
- **AND** the post-admission ownership check, pending enqueue, and upstream send
  are mutually exclusive with stale-session retirement

#### Scenario: Unregistered stale bridge reference cannot submit after admission

- **WHEN** an HTTP bridge request or prewarm holds a stale bridge session
  reference
- **AND** that bridge session is no longer the registered local owner for its
  session key
- **THEN** the request is rejected after response-create gate admission and
  before it is appended or sent upstream
