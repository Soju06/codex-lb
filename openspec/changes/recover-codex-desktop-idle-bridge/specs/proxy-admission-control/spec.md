## MODIFIED Requirements

### Requirement: Stuck HTTP bridge response-create gate sessions are retired

The proxy MUST retain the existing waiter-triggered retirement behavior for stale HTTP bridge response-create gate owners and MUST additionally enforce an owner-side deadline for a visible HTTP request whose current upstream stream does not produce `response.created`, whether the stream remains completely eventless or later emits matched `response.*` lifecycle activity without a response-created milestone. The owner-side deadline MUST be measured from a monotonic timestamp recorded immediately before the current upstream send, MUST use the smaller of the configured stuck-gate retirement threshold and 60 seconds, MUST run without a second gate waiter, and MUST remain active when periodic SSE keepalives are disabled.

The owner-side watchdog MUST apply only while the request owns the response-create gate, awaits `response.created`, has neither a response id nor recorded `response.created` latency, and has produced no downstream-visible output or sequence evidence. Before any matched `response.*` lifecycle event, the deadline MUST remain anchored to the current upstream send; non-response telemetry such as `codex.rate_limits` MUST NOT suppress or extend it. If matched `response.*` lifecycle events arrive without `response.created`, the watchdog MUST remain armed and re-anchor from the most recent upstream response-lifecycle activity instead of the original send. A response-created milestone or downstream-visible evidence MUST suppress this narrow watchdog and leave existing timeout behavior unchanged.

When the owner-side deadline expires, the proxy MUST recheck eligibility and emit a structured low-cardinality log and the existing stuck-retirement Prometheus counter. For requests that are not eligible for the bounded fresh-hard recovery defined by `recover-fresh-hard-bridge-timeouts`, it MUST terminally fail and settle every pending request exactly once, retire the whole bridge session, and MUST NOT transparently replay the timed-out request or move it to another account. An eligible fresh hard request MAY take that single bounded recovery path; if recovery is unavailable or fails, it MUST fall back to the same terminal fail-closed retirement. Neither path may write an account-health failure solely because `response.created` was missing.

#### Scenario: Lone eventless gate owner is retired before the client timeout

- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** its current `response.create` send produced no matched `response.*` event, response id, or downstream-visible output
- **AND** no second request waits for the gate
- **WHEN** the smaller of the configured stuck threshold and 60 seconds elapses after the current send
- **THEN** the proxy emits an explicit terminal failure and retires the bridge session when the request is not eligible for bounded fresh-hard recovery
- **AND** an eligible fresh hard request instead follows the single bounded recovery defined by `recover-fresh-hard-bridge-timeouts`
- **AND** recovery occurs before the native client's 300-second parsed-event idle timeout

#### Scenario: Send time rather than request age anchors the deadline

- **GIVEN** a request spends most of its budget waiting for admission before it sends `response.create`
- **WHEN** the upstream send succeeds
- **THEN** the owner-side deadline begins from that current send
- **AND** earlier queue or admission time does not make the request immediately stale

#### Scenario: Leading telemetry does not mask an eventless owner

- **GIVEN** a pre-created gate owner receives `codex.rate_limits` but no matched `response.*` lifecycle event
- **WHEN** the owner-side deadline elapses
- **THEN** the telemetry does not refresh or suppress the deadline
- **AND** the proxy fails and retires the session

#### Scenario: Response lifecycle evidence re-anchors the missing-created watchdog

- **GIVEN** a pre-created request receives matched `response.*` lifecycle events but no response id, recorded `response.created` latency, or downstream-visible output
- **WHEN** a new response-lifecycle event arrives
- **THEN** the watchdog deadline is re-anchored from the most recent upstream response-lifecycle activity
- **AND** the watchdog remains armed until response-created or downstream-visible evidence appears

#### Scenario: Response-created or visible evidence suppresses the narrow watchdog

- **GIVEN** a pre-created request receives a response id, recorded `response.created` latency, or downstream-visible output
- **WHEN** the eventless owner-side deadline would otherwise elapse
- **THEN** this watchdog does not retire the session
- **AND** existing stream, request-budget, and waiter-triggered timeout behavior remains authoritative

#### Scenario: Timeout is fail-closed and account-neutral

- **GIVEN** an eventless pre-created owner reaches the owner-side deadline
- **WHEN** terminal cleanup runs
- **THEN** every pending request is settled exactly once and the whole session is retired
- **AND** the proxy does not replay the timed-out request or submit it on another account unless it satisfies the bounded fresh-hard recovery requirement
- **AND** the selected account is not marked unhealthy solely because `response.created` was missing
