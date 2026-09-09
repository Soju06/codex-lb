## MODIFIED Requirements

### Requirement: Stuck HTTP bridge response-create gate sessions are retired
When a visible HTTP bridge request times out waiting for a per-session response-create gate, the proxy MUST retire the bridge session only if pending visible request age meets or exceeds the configured stuck-gate retirement threshold, and only while that pending visible request has not yet received `response.created` (no response id and no recorded `response.created` latency) and has not produced downstream-visible output. Receiving a non-visible upstream event before `response.created`, including `codex.rate_limits`, MUST NOT by itself suppress retirement because such an event neither assigns the response nor releases the gate. The retirement MUST emit a structured low-cardinality log and a Prometheus counter without raw keys or prompt content. Pre-created `response.*` lifecycle activity MUST count as response progress and re-anchor the stuck-gate silence clock to the most recent upstream response-lifecycle event, so an actively progressing pre-created request is not retired even when it has not yet produced downstream-visible text. If the timing-out waiter has hard affinity and remains definitively unsubmitted, with no upstream response or downstream sequence markers, the proxy MUST acquire a fresh bridge and submit that waiter once within its original request deadline; a non-zero client-visible replay counter MUST NOT by itself disqualify a waiter from this replacement, since the other definitively-unsubmitted markers already establish the upstream acceptance boundary is unambiguous regardless of replay count. When the replacement is not already required to land on a specific account (no previous-response owner resolved, no file-pinned account), the replacement bridge MUST exclude the account whose gate just proved stuck. A waiter whose replacement is pinned to a required account (a previous-response owner or a file-pinned account) MUST remain pinned to that account, and that required account MUST NOT be excluded on its behalf. The proxy MUST NOT reuse the retired session object or transparently retry an ambiguously submitted request.

The proxy MUST retain the waiter-triggered retirement behavior above for stale HTTP bridge response-create gate owners and MUST additionally enforce an owner-side deadline for a visible HTTP request whose current upstream stream does not produce `response.created`, whether the stream remains completely eventless or later emits matched `response.*` lifecycle activity without a response-created milestone. The owner-side deadline MUST be measured from a monotonic timestamp recorded immediately before the current upstream send, MUST use the smaller of the configured stuck-gate retirement threshold and 60 seconds, MUST run without a second gate waiter, and MUST remain active when periodic SSE keepalives are disabled.

The owner-side watchdog MUST apply only while the request owns the response-create gate, awaits `response.created`, has neither a response id nor recorded `response.created` latency, and has produced no downstream-visible output or sequence evidence. Before any matched `response.*` lifecycle event, the deadline MUST remain anchored to the current upstream send; non-response telemetry such as `codex.rate_limits` MUST NOT suppress or extend it. If matched `response.*` lifecycle events arrive without `response.created`, the watchdog MUST remain armed and re-anchor from the most recent upstream response-lifecycle activity instead of the original send. A response-created milestone or downstream-visible evidence MUST suppress this narrow watchdog and leave existing timeout behavior unchanged.

When the owner-side deadline expires, the proxy MUST recheck eligibility and emit a structured low-cardinality log and the existing stuck-retirement Prometheus counter. For requests that are not eligible for the bounded fresh-hard recovery defined by "Fresh hard bridge requests may recover across accounts", it MUST terminally fail and settle every pending request exactly once, retire the whole bridge session, and MUST NOT transparently replay the timed-out request or move it to another account. An eligible fresh hard request MAY take that single bounded recovery path; if recovery is unavailable or fails, it MUST fall back to the same terminal fail-closed retirement. Neither path may write an account-health failure solely because `response.created` was missing.

#### Scenario: Old pending work blocks a visible gate waiter
- **WHEN** a visible HTTP bridge request receives `response_create_gate_timeout`
- **AND** at least one visible pending request on the same session is older than the configured stuck-gate retirement threshold
- **THEN** the proxy retires the bridge session so later requests can create a fresh session
- **AND** the waiter is rejected cleanly with `response_create_gate_timeout`, unless it has hard affinity and is still definitively unsubmitted, in which case the proxy submits it once on a fresh bridge instead

#### Scenario: Healthy active stream is not retired during a normal wait
- **WHEN** a visible HTTP bridge request times out waiting for the gate
- **AND** the session has no pending visible request older than the configured stuck-gate retirement threshold
- **THEN** the proxy rejects only the waiter
- **AND** the bridge session remains available for the existing in-flight request
- **AND** a pending request that has already received `response.created` or produced downstream-visible output is never classified as a stuck pre-created gate owner, regardless of its age

#### Scenario: Leading rate-limit telemetry does not mask a stuck pre-created request
- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** upstream emits `codex.rate_limits` but never emits `response.created`
- **AND** the pending request becomes older than the configured stuck-gate retirement threshold
- **WHEN** another visible request times out waiting for that gate
- **THEN** the proxy retires the stuck bridge session
- **AND** if the waiter has hard affinity and is still definitively unsubmitted, the proxy submits it once on a fresh bridge
- **AND** the waiter keeps its original deadline and any previous-response account pin

#### Scenario: A reconnected waiter is not disqualified from replacement by its own replay count
- **GIVEN** a gate waiter has already reconnected once (`replay_count` is non-zero)
- **AND** the waiter otherwise has no response id, response event, downstream sequence number, or visible output
- **WHEN** its bridge is retired during gate contention
- **THEN** the proxy still submits that waiter once on a fresh bridge

#### Scenario: Ambiguous waiter is not moved to a replacement bridge
- **GIVEN** a gate waiter has a response event, downstream sequence, visible output, or pending-queue membership
- **WHEN** its bridge is retired during gate contention
- **THEN** the proxy does not transparently submit that waiter on another bridge

#### Scenario: Replacement bridge excludes the account that just proved stuck
- **GIVEN** an unpinned gate waiter (no previous-response owner, no file-pinned account) is accepted for replacement after its session is retired
- **WHEN** the proxy builds the replacement bridge session
- **THEN** account selection for that replacement excludes the retired session's account

#### Scenario: A pinned waiter's replacement keeps its required account, unexcluded
- **GIVEN** a gate waiter's replacement is required to land on a previous-response owner or a file-pinned account
- **AND** that required account is the same account whose gate just proved stuck
- **WHEN** the proxy builds the replacement bridge session
- **THEN** the replacement remains pinned to that required account
- **AND** that account is not added to the request's excluded-account set

#### Scenario: Pre-created response lifecycle activity is not retired
- **GIVEN** a pending HTTP bridge request has not received `response.created`
- **BUT** upstream is emitting `response.*` lifecycle events for that request
- **WHEN** another visible request times out waiting for the gate
- **THEN** the proxy does not retire the actively progressing request

#### Scenario: Lone eventless gate owner is retired before the client timeout
- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** its current `response.create` send produced no matched `response.*` event, response id, or downstream-visible output
- **AND** no second request waits for the gate
- **WHEN** the smaller of the configured stuck threshold and 60 seconds elapses after the current send
- **THEN** the proxy emits an explicit terminal failure and retires the bridge session when the request is not eligible for bounded fresh-hard recovery
- **AND** an eligible fresh hard request instead follows the single bounded recovery defined by "Fresh hard bridge requests may recover across accounts"
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
