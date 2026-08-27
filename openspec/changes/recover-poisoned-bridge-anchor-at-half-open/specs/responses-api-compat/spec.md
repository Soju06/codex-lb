# responses-api-compat Delta Specification

## MODIFIED Requirements

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`).

A bridge retirement MUST record one of those failures only when the retiring
session still owns at least one pending request and no response event has been
observed for that request lifecycle. Retiring an idle upstream bridge with no
pending request MUST NOT advance the circuit or cause a later request to be
treated as a repeated failure. A pending request that has already emitted a
response event MUST remain excluded from this pre-response circuit. An
upstream terminal error frame that fails a pending request before any
response event was observed, and that leaves the request with no safe replay,
MUST record one failure for that request
lifecycle through the same attempt-scoped recorder, because that failure
settles through the terminal path rather than a retirement and would
otherwise never advance the circuit; a later retirement of the same lifecycle
MUST NOT count it again. A failure the proxy can still replay safely MUST NOT
advance the circuit: the request is not stranded, the verified stale-anchor
replay that follows depends on the circuit generation it captured, and counting
there both disturbs that fence and charges the key for a failure it recovered
from in band. This exclusion MUST apply identically when one terminal frame
settles a grouped fan-out of requests, so a group whose members can each still
replay safely cannot advance the circuit between them. A native terminal failure envelope
(`response.failed` or `response.incomplete`) MUST remain eligible for that
recording even though it marks the `response.create` attempt as answered
without counting a response event. The recording MUST complete before the
terminal frame and its end-of-stream sentinel are published downstream, so a
client that resends the moment it observes completion cannot have that resend
planned while the resulting cooldown and quarantine are still being written.
The grouped multi-request continuity settlement, which fails several pending
requests with synthetic terminal events and returns before that path, MUST
record one failure for each grouped request that observed no response event,
under the same ordering rule.

When such a strike opens the circuit on a poison-class detail, the proxy MUST
also clear the stored durable continuity anchor for that key. The quarantine
armed with the strike only suppresses injection in this process and expires,
so without the durable clear the same dead anchor is restored on the next
reattach and re-poisons the key after every cooldown. On the terminal and grouped settlement
paths the configured anchor-poison threshold MUST be capped at the circuit's own
failure threshold. Above that threshold the key is refused for 60-600s per
strike, so a higher value cannot be reached at any useful rate. A configured
value below the circuit threshold MUST still be honoured. The retirement and
close funnels keep the configured threshold unchanged, because no circuit gates
those paths first.

A grouped settlement whose strikes carry the circuit through that threshold
MUST clear the anchor as well, after its grouped terminal frames are published.

Unlike the strike, the durable clear MUST NOT precede the terminal frame; a
resend arriving in that window is already covered by the quarantine. Because
the frame has already been published, a cancellation escaping the clear MUST
NOT skip finalization of the settled request.

A quarantine armed from a local opening MUST be re-armed against the merged
cooldown when durable persistence returns a longer deadline, so its floor
covers the cooldown actually in force rather than the local backoff it was
first computed from.

A confirmed durable anchor abandonment MUST settle the retry circuit for that
key. The circuit was opened by failures against the anchor the abandonment
removed, so its cooldown would otherwise back off a cause that no longer
exists and refuse requests that carry no anchor at all. The abandonment is the
same proof of recovery a completed response carries. An abandonment that was
fenced or failed proves nothing and MUST leave the cooldown running, and so
does one whose requests can still be replayed safely: such a request is about
to be retried and claims the circuit's generation at dispatch, so the circuit
must survive for it. Only an abandonment that leaves every request it covers
stranded may settle.

That settle MUST remove the durable row even when this worker holds no version
fence for it. A circuit opened and remediated in the same instant has not been
persisted yet, and the fence guard would otherwise skip the delete, leaving a
row that the next load rehydrates into a cooldown whose cause is gone. The
fence MUST still apply to callers that merely observed the circuit rather than
removing its cause.

Once the key is quarantined for a poisoned anchor, the local previous-response
rebind MUST NOT re-attach to the rejected anchor. The quarantine registry is
shared with the wedged-reattach and repeated-eventless fences, which fence the
session without evidence about its anchor, so this rebind MUST test the
recorded quarantine reason rather than the presence of an active quarantine
window; an explicit rejection arriving during either of the other two fences
MUST keep the anchor. An explicit rejection on its
own does not prove the anchor dead, since it can mean the session was not its
owner, so the rebind's existing same-anchor retry MUST be preserved until the
circuit has opened on repeated eventless poison-class failures. After that the
rebind MUST retry unanchored while keeping its interrupted tool-output
injection, so the recovery it exists to perform is not lost.

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

When the cooldown expires, the proxy MUST admit exactly one probe request and
MUST keep suppressing other non-bypassed requests for that key while that
probe may still be running. When the circuit opens on an eventless
poison-class failure (`stream_incomplete` or `stream_idle_timeout` with no
observed response event), the proxy MUST quarantine the session key as
specified under the silent-session quarantine requirement, so the probe
admitted after the cooldown is planned without the anchor the circuit opened
on. This MUST hold however the circuit reached its threshold: when concurrent
replicas each record a locally-first failure and the durable conflict merge is
what opens the circuit, the recording replica MUST re-evaluate the quarantine
against the merged state, because no replica observed the threshold under its
own lock. That re-evaluation MUST turn on the merged opening itself and not on
the cooldown it leaves: a merge can adopt a cooldown that has already elapsed,
and such a key is at its threshold with no cooldown left, so the next request
is the half-open probe the quarantine exists to protect. A `clean_close` opening MUST NOT quarantine the key.

When the proxy suppresses a submission, the `retry_after_seconds` it returns
and the detail it logs MUST reflect the timer that is actually refusing the
request: the cooldown while the cooldown is active (`hard_key_cooldown`), and
the half-open lease once the cooldown has expired (`hard_key_half_open`). The
suppression message MUST NOT describe the bridge as cooling down when the
cooldown has expired.

The clean-close retry jitter maximum MUST be read from the
`http_responses_session_bridge_clean_close_retry_jitter_max_seconds` runtime
setting and MUST be bounded to the inclusive range 0–30 seconds.

The proxy MUST evict process-local circuit entries and their loaded/persisted
markers after one hour without use, independently of durable-row cleanup, so
one-shot hard-affinity keys cannot grow the worker's memory without bound.

Before every hard-affinity retry decision, the proxy MUST refresh the durable
row so a cooldown opened by another replica is observed even when this process
has already loaded the key. A durable lookup or persistence failure MUST NOT
crash the request; the proxy MUST continue using available local state and
record the failure for observability. Rows older than one hour MUST be treated
as expired and removed. A successful terminal response MUST clear the local
and durable circuit state.

#### Scenario: idle bridge retirement does not consume a circuit strike

- **GIVEN** a hard-affinity HTTP bridge has no pending requests
- **WHEN** its upstream WebSocket closes and the idle bridge is retired
- **THEN** the retry-circuit failure count for that key remains unchanged
- **AND** a later request is not placed in cooldown because of the idle close

#### Scenario: eventless pending retirement consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** the bridge retires because the upstream fails before acknowledging the request
- **THEN** the retry circuit records exactly one failure for that request lifecycle

#### Scenario: eventless terminal error frame consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** upstream fails that request with a terminal error frame (for example a rewritten `previous_response_not_found`) before any response event
- **THEN** the retry circuit records exactly one failure for that request lifecycle
- **AND** a subsequent retirement of the same lifecycle does not record a second failure

#### Scenario: native terminal failure envelope consumes a strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no counted response event
- **WHEN** upstream fails it with a native `response.failed` envelope that never sent `response.created`
- **THEN** the envelope still consumes one attempt-scoped retry-circuit strike
- **AND** two such envelopes on the same key open the circuit and quarantine it with reason `retry_circuit_poisoned_anchor`

#### Scenario: the terminal strike lands before the client observes completion

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no counted response event
- **WHEN** upstream fails it with an eventless terminal error frame
- **THEN** the retry-circuit failure is recorded before the terminal frame or its end-of-stream sentinel reaches the downstream queue
- **AND** the terminal frame is still published to the client afterwards

#### Scenario: grouped continuity failure records one strike per eventless request

- **GIVEN** a hard-affinity HTTP bridge with several pending requests sharing one anchor
- **WHEN** upstream reports `previous_response_not_found` and the grouped settlement fails them all with synthetic terminal events
- **THEN** each grouped request that observed no response event records one attempt-scoped strike
- **AND** the strikes are recorded before the grouped terminal events are persisted or delivered

#### Scenario: a terminal poison strike clears the durable anchor

- **GIVEN** a hard-affinity bridge key whose stored anchor upstream has rejected
- **WHEN** a terminal failure frame opens the retry circuit on a poison-class detail
- **THEN** the durable continuity anchor for that key is cleared with its alias rows
- **AND** the clear runs after the terminal frame reaches the client, not before it

#### Scenario: grouped poison strikes clear the durable anchor

- **GIVEN** a grouped continuity failure carrying two eventless requests on one hard key
- **WHEN** the grouped strikes carry the circuit through its threshold
- **THEN** the durable anchor is cleared after the grouped terminal frames are published

#### Scenario: a cancelled anchor clear still finalizes the settled request

- **GIVEN** a terminal poison strike whose durable clear is cancelled mid-write
- **WHEN** the terminal frame has already been published to the client
- **THEN** the request is still finalized and its session lease still released

#### Scenario: a merged cooldown extends an already-armed quarantine

- **GIVEN** a local opening that armed the poison quarantine from its own backoff
- **WHEN** durable persistence merges in a longer cooldown deadline
- **THEN** the quarantine floor is recomputed against the merged cooldown

#### Scenario: abandoning the anchor settles the circuit it invalidated

- **GIVEN** a hard-affinity key whose retry circuit is cooling from poison-class failures
- **WHEN** the durable anchor those failures hit is successfully abandoned
- **THEN** the retry circuit for that key is cleared rather than left cooling
- **AND** a fenced or failed abandonment leaves the cooldown running
- **AND** the durable row is deleted even when no version fence has been stamped for it

#### Scenario: the local rebind drops an anchor the circuit has proven dead

- **GIVEN** a key quarantined for a poisoned anchor after repeated eventless poison-class failures
- **WHEN** an anchored request fails with an explicit previous-response rejection and enters local rebind
- **THEN** the rebind retries with no `previous_response_id` rather than re-attaching
- **AND** an explicit rejection on a key that is not quarantined still retries the same anchor

#### Scenario: midstream retirement does not consume a pre-response strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with an observed response event
- **WHEN** the bridge retires before completion
- **THEN** the pre-response retry-circuit failure count remains unchanged

#### Scenario: the second hard-key failure opens a durable circuit

- **GIVEN** a hard-affinity key has one recorded pre-response failure
- **WHEN** a second eligible failure is recorded
- **THEN** the proxy opens the retry circuit
- **AND** persists at least two consecutive failures and a cooldown deadline
- **AND** subsequent pre-created replay is suppressed until that deadline

#### Scenario: circuit opened by eventless failures quarantines the key

- **GIVEN** a hard-affinity key has one recorded eventless `stream_incomplete` failure
- **WHEN** a second eventless `stream_incomplete` failure opens the circuit
- **THEN** the session key is quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** the next full-resend request on that key is planned without the durable anchor

#### Scenario: a circuit opened by the durable merge still quarantines the key

- **GIVEN** concurrent replicas that each record only their locally-first eventless `stream_incomplete` failure, so neither reaches the threshold under its own lock
- **WHEN** the durable conflict merge raises the recording replica's view to the threshold and opens the cooldown
- **THEN** that replica re-evaluates the quarantine against the merged state
- **AND** the session key is quarantined with reason `retry_circuit_poisoned_anchor`

#### Scenario: a merged opening whose cooldown already elapsed still quarantines

- **GIVEN** another replica opened the circuit long enough ago that its cooldown deadline is already in the past
- **WHEN** this worker's durable write merges that state and raises it to the threshold with no cooldown remaining
- **THEN** the key is still quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** the quarantine covers the half-open lease, because the next request on that key is the probe

#### Scenario: circuit opened by clean closes does not quarantine the key

- **GIVEN** a hard-affinity key has one recorded `clean_close` failure
- **WHEN** a second `clean_close` failure opens the circuit
- **THEN** the session key is not quarantined

#### Scenario: suppression reports the half-open lease after the cooldown expires

- **GIVEN** a hard-affinity key's cooldown has expired and a probe holds the half-open lease
- **WHEN** another request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining half-open lease
- **AND** the circuit event detail is `hard_key_half_open`
- **AND** the message does not describe the bridge as cooling down

#### Scenario: suppression reports the cooldown while cooling

- **GIVEN** a hard-affinity key's cooldown is active
- **WHEN** a request for that key is suppressed
- **THEN** the 503 `retry_after_seconds` reflects the remaining cooldown
- **AND** the circuit event detail is `hard_key_cooldown`

#### Scenario: retry decisions observe a cooldown opened by another replica

- **GIVEN** this replica previously looked up a hard-affinity key with no row
- **AND** another replica persists an open cooldown for that same key and API-key scope
- **WHEN** this replica evaluates the next pre-created retry
- **THEN** it refreshes durable state before deciding
- **AND** suppresses the retry for the persisted cooldown

#### Scenario: circuit state remains isolated by key and API-key scope

- **GIVEN** one hard-affinity key has an open circuit
- **WHEN** a different affinity key or API-key scope evaluates a retry
- **THEN** that request is not suppressed by the first key's circuit

#### Scenario: durable circuit lookup failure does not fail the request

- **GIVEN** durable retry-circuit lookup or persistence is unavailable
- **WHEN** the proxy evaluates or records a retry-circuit event
- **THEN** the request continues using any available local circuit state
- **AND** the failure is logged and exposed through retry-circuit observability

### Requirement: Repeated zero-event idle failures poison dead anchors

For hard HTTP bridge keys, repeated zero-event idle failures MUST use the
existing durable retry-circuit counter to identify an anchor that should no
longer remain addressable. When consecutive failures for the same hard bridge
key reach the configured poison threshold, the proxy MUST abandon durable
continuity for that session and retire the bridge even when admission waiters
exist. The default threshold MUST be no greater than seven failures.

Recovery MUST NOT depend on the counter reaching that threshold: because an
open circuit admits only one probe per half-open lease, the counter may never
reach a threshold above the circuit's own opening threshold from an interactive
client. The circuit opening on an eventless poison-class failure MUST
therefore quarantine the key independently, as specified under the
silent-session quarantine requirement, so a full-resend probe after the
circuit opens is planned without the dead anchor.

#### Scenario: Admission waiters cannot defer anchor poisoning forever

- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor

#### Scenario: A dead anchor is bypassed before the poison threshold is reached

- **GIVEN** a hard durable bridge key has two consecutive eventless `stream_incomplete` failures and an open circuit
- **AND** the configured poison threshold is greater than two
- **WHEN** the cooldown expires and the next full-resend request is admitted as the probe
- **THEN** the key is quarantined and the probe is planned without the dead anchor
- **AND** the probe resends full history rather than the dead anchor

#### Scenario: Lease liveness comparison is timezone-safe
- **GIVEN** a durable bridge session whose `lease_expires_at` was read from a `timestamptz` column (offset-aware) on PostgreSQL
- **WHEN** the dead-owner classifier evaluates lease liveness against the application's naive-UTC clock
- **THEN** both timestamps MUST be normalized to naive UTC before comparison
- **AND** the anchored-lookup path MUST NOT raise on mixed-awareness datetimes

### Requirement: Silent HTTP bridge sessions are quarantined from re-attach and reuse

When an HTTP bridge session proves silent/wedged, the proxy MUST quarantine its session key for a bounded window so later requests stop attaching to it. A session proves silent/wedged when either (a) a pending request being failed or retired carried a proxy-injected `previous_response_id`, had sent `response.create`, observed upstream response events, and never had `response.created` assigned, (b) the session key hits two consecutive eventless `missing_response_created_timeout` retires, or (c) the hard-affinity retry circuit for the key opens on an eventless poison-class failure (`stream_incomplete` or `stream_idle_timeout`), in which case the quarantine reason MUST be `retry_circuit_poisoned_anchor`. This holds for every path that fails or retires the request — partial stale-holder cleanup, the reader-failure funnel, and direct all-stale session retirement alike. The quarantine MUST be evaluated only when a request is already being failed or its session retired — never against a live owned turn — so a stream whose `response.created` was observed (including deferred-reasoning streams with long event gaps) MUST NOT be quarantined, and mere event silence during an owned live turn MUST NOT trigger quarantine by itself.

While a session key is quarantined: an existing session under that key MUST NOT be selected for reuse (a new request detaches it and proceeds on a fresh session), and for durable-anchor selection a quarantined session that is still open MUST count as absent, exactly as if it were already gone. The quarantine registry verdict is authoritative for the key: any session under the key while the quarantine window is active — including a freshly created replacement whose own completion has not yet cleared the quarantine — is equally excluded from reuse and equally absent for anchor selection. A fresh reattach whose incoming payload already looks like a full conversation resend MUST NOT receive a proxy-injected durable anchor through any injection point — the fresh-reattach injection, session-state hydration of the durable anchor, or the session-level injection — so the dispatch goes upstream genuinely unanchored with the client's own untrimmed payload. A payload that does not look like a full resend (a genuine delta-only continuation) MUST still receive the durable anchor, because it has no other way to convey prior conversation state.

Quarantine state MUST be bounded and self-recovering: it is in-memory and session-scoped, expires by TTL (a live session that outlives its quarantine window MUST become reusable again), is cleared when a response completes on the same session key, and MUST NOT write account health or alter account selection.

A quarantine armed for reason `retry_circuit_poisoned_anchor` MUST remain in force for at least the remaining cooldown of the circuit that armed it plus that circuit's half-open lease, because the probe it exists to protect is only admitted once that cooldown expires and may then be admitted anywhere inside the lease that follows. The default TTL alone MUST NOT be relied on for this: it equals the circuit's maximum cooldown, so at that cooldown the quarantine would otherwise lapse in the same instant the cooldown does and hand the poisoned anchor back to the very request the cooldown was holding.

#### Scenario: Reattach streams events but response.created is never assigned (#1534)

- **GIVEN** a durable HTTP bridge session with a stored anchor whose fresh reattach injected a proxy-owned `previous_response_id`
- **AND** the reattached upstream stream delivers response events but `response.created` is never assigned
- **WHEN** the stream fails or the session is retired with that request still pending
- **THEN** the request fails terminally as before
- **AND** the session key is quarantined with reason `reattach_missing_response_created`

#### Scenario: All-stale direct retirement still quarantines the key

- **GIVEN** a wedged reattach (proxy-injected `previous_response_id`, `response.create` sent, response events observed, `response.created` never assigned) that is the ONLY stale pending request on its session
- **WHEN** the stuck-gate watchdog retires the session directly instead of failing the stale holder individually
- **THEN** the session key is quarantined with reason `reattach_missing_response_created`
- **AND** the next request takes the fresh no-anchor path instead of rebuilding the identical anchored reattach

#### Scenario: Next request after the wedge completes on the fresh path

- **GIVEN** a session key quarantined after a reattach that streamed events without `response.created`
- **WHEN** a later request arrives for the same key with a full-conversation-resend payload and no client `previous_response_id`
- **THEN** the proxy does not inject the durable anchor for that request
- **AND** the request is sent upstream unanchored with the client's own full payload
- **AND** the request can complete normally instead of rebuilding the identical wedged reattach

#### Scenario: Suppressed anchor does not come back through session state

- **GIVEN** a quarantined session key and a full-conversation-resend payload whose stored durable prefix is trimmable but whose fresh suffix does not retain the prior output
- **WHEN** the fresh-reattach durable-anchor injection is skipped because of the quarantine
- **THEN** the durable anchor is not rehydrated into the fresh session's completed-response state
- **AND** the session-level injection does not re-add the same anchor or trim the stored prefix
- **AND** the dispatch goes upstream genuinely unanchored with the client's untrimmed payload
- **AND** the suppression applies even when the fresh-reattach injection was already ineligible for other reasons (for example a conversation-scoped payload, a live alias session, or an active-owner forward that falls back to a local rebind)

#### Scenario: A poison quarantine outlives the cooldown that armed it

- **GIVEN** repeated eventless poison-class failures have driven a hard-affinity circuit to its maximum cooldown
- **WHEN** the quarantine is armed with reason `retry_circuit_poisoned_anchor` at that same instant
- **THEN** the quarantine window extends past the cooldown deadline by at least the circuit's half-open lease
- **AND** the probe admitted once that cooldown expires is still planned without the poisoned anchor

#### Scenario: Quarantined session is excluded from reuse selection

- **GIVEN** a session marked quarantined that is still live or retained for admission handoff
- **WHEN** a new request looks up that session key
- **THEN** the session is not considered reusable
- **AND** the request proceeds on a fresh session instead
- **AND** a replacement session created under the same still-quarantined key is likewise not reusable until a completion or the TTL clears the quarantine

#### Scenario: Repeated eventless timeouts quarantine the key

- **GIVEN** a session key whose pending request already retired once with the eventless `missing_response_created_timeout`
- **WHEN** a subsequent attach on the same key retires with the same eventless timeout before any response completes on the key
- **THEN** the session key is quarantined with reason `repeated_eventless_timeout`
- **AND** the first timeout alone does not quarantine the key

#### Scenario: Deferred-reasoning live turn is never quarantined

- **GIVEN** an owned live turn whose `response.created` was observed and whose events flow with long gaps (deferred reasoning)
- **WHEN** its stream later fails or its session is retired
- **THEN** the session key is not quarantined
- **AND** later requests keep the existing reuse and anchor-injection behavior

#### Scenario: Delta-only payloads keep their anchor while quarantined

- **GIVEN** a quarantined session key — including one whose quarantined session is still open with other active requests
- **WHEN** a later request arrives whose payload does not look like a full conversation resend
- **THEN** the still-open quarantined session counts as absent for durable-anchor selection
- **AND** the durable anchor is still injected for that request, preserving the client's only way to convey prior context

#### Scenario: Quarantine is bounded and self-clearing

- **GIVEN** a quarantined session key
- **WHEN** a response completes on that session key, or the quarantine TTL elapses
- **THEN** the quarantine (and its eventless strike counter) is cleared
- **AND** a session that survived the quarantine window is reusable again instead of staying rejected forever
- **AND** no durable row, janitor work, or account-health write was involved at any point

#### Scenario: Retry circuit opened by eventless failures quarantines the key

- **GIVEN** a hard-affinity bridge key whose retry circuit has one recorded eventless `stream_incomplete` failure
- **WHEN** a second eventless `stream_incomplete` failure opens the circuit
- **THEN** the session key is quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** a subsequent full-resend request on that key is dispatched unanchored through the existing fresh path
- **AND** a subsequent delta-only request on that key still receives the durable anchor

#### Scenario: Retry circuit opened by clean closes leaves the key unquarantined

- **GIVEN** a hard-affinity bridge key whose retry circuit has one recorded `clean_close` failure
- **WHEN** a second `clean_close` failure opens the circuit
- **THEN** the session key is not quarantined
