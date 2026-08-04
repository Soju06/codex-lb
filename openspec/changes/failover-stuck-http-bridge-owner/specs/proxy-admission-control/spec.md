## MODIFIED Requirements

### Requirement: Stuck HTTP bridge response-create gate sessions are retired

When a visible HTTP bridge request times out waiting for a per-session
response-create gate, the proxy MUST retire the bridge session only if a
pending visible request still owns the gate, is still awaiting
`response.created`, has not produced downstream-visible output, and its age
meets or exceeds the configured stuck-gate retirement threshold. Receiving a
non-visible upstream event before `response.created`, including
`codex.rate_limits`, MUST NOT by itself suppress retirement because such an
event neither assigns the response nor releases the gate. The retirement MUST
emit a structured low-cardinality log and a Prometheus counter without raw keys
or prompt content. Pre-created `response.*` lifecycle activity MUST count as
response progress and suppress stuck-gate retirement even when it has not yet
produced downstream-visible text. If the timing-out waiter has hard affinity
and remains definitively unsubmitted, with no upstream response or downstream
sequence markers, the proxy MUST acquire a fresh bridge and submit that
waiter once within its original request deadline; a non-zero client-visible
replay counter MUST NOT by itself disqualify a waiter from this replacement,
since it reflects client-side reconnect attempts rather than upstream
progress on the current bridge attempt. When the waiter has no
previous-response account pin, the replacement bridge MUST exclude the
account whose gate just proved stuck. An anchored waiter MUST remain pinned
to the previous-response owner account. The proxy MUST NOT reuse the retired
session object or transparently retry an ambiguously submitted request.

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

- **GIVEN** a gate waiter with no previous-response account pin is accepted for replacement after its session is retired
- **WHEN** the proxy builds the replacement bridge session
- **THEN** account selection for that replacement excludes the retired session's account
- **AND** a continuity-pinned waiter's replacement remains pinned to its required account instead

#### Scenario: Healthy active stream is not retired during a normal wait

- **GIVEN** a pending HTTP bridge request has received `response.created` or produced downstream-visible output
- **WHEN** another visible request times out waiting for the gate
- **THEN** the proxy does not classify the active stream as a stuck pre-created gate owner

#### Scenario: Pre-created response lifecycle activity is not retired

- **GIVEN** a pending HTTP bridge request has not received `response.created`
- **BUT** upstream is emitting `response.*` lifecycle events for that request
- **WHEN** another visible request times out waiting for the gate
- **THEN** the proxy does not retire the actively progressing request
