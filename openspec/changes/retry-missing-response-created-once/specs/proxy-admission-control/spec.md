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
attempt a second replay.

#### Scenario: Lone eventless gate owner recovers on a fresh socket

- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** its current send has no matched `response.*` event, response id, or
  downstream-visible output
- **WHEN** the smaller of the configured stuck threshold and 30 seconds elapses
- **THEN** the proxy cancels the stale receive and safely replays the request at
  most once on a fresh upstream socket
- **AND** a successful replay continues the original downstream stream

#### Scenario: Completed receive wins cancellation

- **GIVEN** an eventless gate owner reaches its deadline
- **WHEN** its pending upstream receive completes as cancellation is attempted
- **THEN** the proxy processes the completed receive through the normal path
- **AND** it does not replay the request

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
