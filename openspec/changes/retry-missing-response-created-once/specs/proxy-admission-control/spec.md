## MODIFIED Requirements

### Requirement: Stuck HTTP bridge response-create gate sessions are retired

The proxy MUST retain the existing waiter-triggered retirement behavior for
stale HTTP bridge response-create gate owners and MUST additionally enforce an
owner-side deadline for a visible HTTP request whose current upstream
`response.create` send remains completely eventless before `response.created`.
The owner-side deadline MUST be measured from a monotonic timestamp recorded
immediately before the current upstream send, MUST use the smaller of the
configured stuck-gate retirement threshold and 30 seconds, MUST run without a
second gate waiter, and MUST remain active when periodic SSE keepalives are
disabled.

The owner-side watchdog MUST apply only while the request owns the
response-create gate, awaits `response.created`, has neither a response id nor
recorded `response.created` latency, has received no matched `response.*`
lifecycle event, and has produced no downstream-visible output or sequence
evidence. Non-response telemetry such as `codex.rate_limits` MUST NOT suppress
this watchdog. Any matched `response.*` lifecycle event, response-created
milestone, or downstream-visible evidence MUST suppress the owner-side
watchdog and leave existing timeout behavior unchanged.

Before invoking an upstream websocket send primitive, each supported websocket
adapter MUST check whether its connection is already closed. A closed adapter
MUST return an unforgeable not-dispatched result and MUST NOT invoke the send
primitive. When the HTTP bridge receives that result before any response
lifecycle or downstream-visible evidence, it MUST reconnect and send the exact
request at most once on the same leased account. This recovery MAY preserve a
client continuation anchor because the not-dispatched result proves that the
upstream did not receive the failed attempt. The replacement attempt MUST
remain within the original request budget and existing replay limit.

The direct WebSocket proxy MUST apply the same closed-before-send recovery only
when the undispatched request is the socket's sole pending request. It MUST
remove that request from the retired reader before cancellation, retain its
response-create admission and account lease, require the same account for the
replacement connection, preserve the exact request including any continuation
anchor, and re-register it on the replacement reader. A sibling request,
response lifecycle evidence, downstream-visible output, or an exhausted replay
count MUST suppress this recovery and retain existing terminal settlement.

Any failure raised after the adapter invokes its send primitive remains
dispatch-ambiguous and MUST retain the existing fail-closed behavior without an
internal resend.

When the first owner-side deadline expires, the proxy MUST recheck eligibility,
cancel the stale receive wait, and attempt one transparent replay only through
the existing pre-created replay safety and ownership rules and only when the
eventless owner is the session's sole pending request. If the receive completes
while cancellation is attempted, the proxy MUST process that result and MUST
NOT replay the request. A replay MUST use the account whose response-create
concurrency lease the request holds. Hard-affinity work MUST remain on the
required account, account-scoped file ownership MUST be preserved, and a
continuation MUST be replayed only from an explicitly retained retry-safe
full-resend body. The retry MUST NOT extend the original request budget or mark
the selected account unhealthy solely because `response.created` was missing.

If replay is unsafe, reconnect/resend fails, or the replacement send reaches
the deadline, the proxy MUST emit a structured low-cardinality timeout log and
the existing stuck-retirement Prometheus counter, terminally settle every
pending request exactly once, and retire the whole bridge session. It MUST NOT
attempt a second replay. Cancellation of the relay owner or closure of the
session MUST suppress reconnect and resend even when that cancellation is
observed while awaiting the stale receive task's cancellation.

#### Scenario: Lone eventless gate owner recovers on a fresh socket

- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** its current send has no matched `response.*` event, response id, or
  downstream-visible output
- **WHEN** the smaller of the configured stuck threshold and 30 seconds elapses
- **THEN** the proxy cancels the stale receive and safely replays the request at
  most once on a fresh upstream socket
- **AND** a successful replay continues the original downstream stream

#### Scenario: Closed warm socket recovers before dispatch

- **GIVEN** a warm bridge socket closed normally before a compacted
  continuation starts sending
- **WHEN** the adapter checks the connection before invoking its send primitive
- **THEN** it returns the sealed not-dispatched result without sending
- **AND** the bridge reconnects once on the same leased account
- **AND** it sends the exact continuation through the original downstream
  stream without requiring a client reconnect

#### Scenario: Mid-send failure remains ambiguous

- **GIVEN** an upstream socket appears open before send
- **WHEN** its send primitive raises after dispatch may have begun
- **THEN** the adapter returns the existing ambiguous transport failure
- **AND** the bridge does not reconnect and resend internally

#### Scenario: Direct WebSocket continuation recovers before dispatch

- **GIVEN** a direct WebSocket continuation is the sole pending request
- **AND** its warm upstream socket is already closed before send dispatch
- **WHEN** the adapter returns the sealed not-dispatched result
- **THEN** the proxy removes the request from the retired reader
- **AND** it reconnects once to the same leased account
- **AND** it resends the exact continuation through the existing downstream
  WebSocket

#### Scenario: Direct WebSocket closed-before-send recovery is bounded

- **GIVEN** a direct WebSocket request already consumed its exact resend
- **WHEN** the replacement socket is also closed before dispatch
- **THEN** the proxy does not attempt another reconnect and resend
- **AND** it applies existing terminal settlement

#### Scenario: Completed receive wins cancellation

- **GIVEN** an eventless gate owner reaches its deadline
- **WHEN** its pending upstream receive completes as cancellation is attempted
- **THEN** the proxy processes the completed receive through the normal path
- **AND** it does not replay the request

#### Scenario: Relay shutdown wins receive cancellation

- **GIVEN** an eventless gate owner reaches its deadline
- **AND** bridge shutdown cancels the relay while it awaits stale receive
  cancellation
- **THEN** the relay propagates its own cancellation
- **AND** it does not reconnect or resend after session ownership is released

#### Scenario: A pending sibling prevents socket replacement

- **GIVEN** an eventless gate owner reaches its deadline
- **AND** another request is still pending on the same upstream socket
- **WHEN** recovery is evaluated
- **THEN** the proxy does not replace the socket for a transparent replay
- **AND** it retains the existing whole-session terminal settlement

#### Scenario: Send time rather than request age anchors each deadline

- **GIVEN** a request spends most of its budget waiting for admission
- **WHEN** the original request or its one replay sends `response.create`
- **THEN** the owner-side deadline begins from that current send
- **AND** prior admission time or the prior attempt does not make the send
  immediately stale

#### Scenario: Leading telemetry does not mask an eventless owner

- **GIVEN** a pre-created gate owner receives `codex.rate_limits` but no matched
  `response.*` lifecycle event
- **WHEN** the owner-side deadline elapses
- **THEN** the telemetry does not refresh or suppress the deadline
- **AND** the proxy applies the same one-replay policy

#### Scenario: Eventless retry keeps its leased account

- **GIVEN** an eventless gate owner holds an account-scoped response-create
  lease
- **WHEN** the owner is safely replayed
- **THEN** the fresh socket uses the same account
- **AND** the resend does not bypass per-account concurrency admission

#### Scenario: Response lifecycle evidence suppresses the narrow watchdog

- **GIVEN** a pre-created request receives any matched `response.*` lifecycle
  event, response id, recorded `response.created` latency, or
  downstream-visible output
- **WHEN** the eventless owner-side deadline would otherwise elapse
- **THEN** this watchdog does not reconnect or retire the session
- **AND** existing stream, request-budget, and waiter-triggered behavior remains
  authoritative

#### Scenario: Unsafe or exhausted recovery fails closed

- **GIVEN** an eventless pre-created owner reaches the owner-side deadline
- **AND** safe replay is unavailable, fails, or has already been attempted
- **WHEN** terminal cleanup runs
- **THEN** every pending request is settled exactly once and the whole session
  is retired
- **AND** the selected account is not marked unhealthy solely because
  `response.created` was missing
- **AND** no second replay is attempted

#### Scenario: Old pending work blocks a visible gate waiter

- **WHEN** a visible HTTP bridge request receives
  `response_create_gate_timeout`
- **AND** at least one visible pending request on the same session is older than
  the configured stuck-gate retirement threshold
- **THEN** the proxy retires the bridge session so later requests can create a
  fresh session
- **AND** the waiter is rejected cleanly with `response_create_gate_timeout`

#### Scenario: Healthy active stream is not retired during a normal wait

- **WHEN** a visible HTTP bridge request times out waiting for the gate
- **AND** the session has no pending visible request older than the configured
  stuck-gate retirement threshold
- **THEN** the proxy rejects only the waiter
- **AND** the bridge session remains available for the existing in-flight
  request
