# proxy-admission-control Specification

## Purpose
Define how the proxy protects itself under load while preserving short request paths and surfacing local overload clearly.
## Requirements
### Requirement: Downstream proxy admission is split by traffic class

The system MUST enforce independent downstream admission limits for proxy HTTP requests, proxy websocket sessions, compact HTTP requests, and dashboard traffic. Exhausting one proxy lane MUST NOT consume capacity from the others.

#### Scenario: Websocket session load does not starve HTTP responses
- **WHEN** the proxy websocket admission lane is full
- **THEN** new websocket sessions are rejected locally
- **AND** eligible proxy HTTP requests may still proceed if their own lane has capacity

#### Scenario: Compact lane survives general proxy load
- **WHEN** the general proxy HTTP lane is saturated
- **AND** the compact lane still has capacity
- **THEN** `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests continue to be admitted

### Requirement: Local overload responses are explicit

When the proxy rejects a request locally because an admission lane or expensive-work stage is full, it MUST return a local-overload response with a `Retry-After` header. HTTP requests MUST use an OpenAI-style error envelope and websocket handshake denials MUST use an HTTP denial response instead of a pre-accept close frame.

#### Scenario: HTTP admission rejection returns explicit overload envelope
- **WHEN** a proxy HTTP request is rejected locally for overload
- **THEN** the response status is `429`
- **AND** the response includes `Retry-After`
- **AND** the error payload identifies the failure as local proxy overload instead of upstream unavailability

#### Scenario: Websocket handshake rejection returns explicit overload status
- **WHEN** a websocket handshake is rejected locally for overload
- **THEN** the client receives an HTTP denial response with the real overload status
- **AND** the server access log reflects that overload status instead of `403 Forbidden`

### Requirement: Expensive upstream work is admission controlled

The proxy MUST enforce separate in-process admission limits for token refresh, upstream websocket connect, and first-turn response creation.

#### Scenario: Owner-switch blocked websocket releases response-create admission

- **GIVEN** a websocket request has acquired response-create admission
- **AND** the request cannot switch to its required previous-response owner because another request is still streaming on the current upstream socket
- **WHEN** the proxy emits `previous_response_owner_unavailable` for the blocked request
- **THEN** it releases that request's response-create gate and account response-create lease
- **AND** later eligible requests are not blocked by stale local response-create pressure

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits, and the configured limits MUST be cluster-wide per-account targets enforced across all replicas rather than per-replica allowances. Because per-account caps are partitioned per replica via the bridge ring and cannot be safely partitioned across intra-pod worker processes, each instance MUST run a single worker process; horizontal scaling is achieved by adding replicas. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value.

When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. A bare process-session mapping MAY supply soft locality only while the request is self-contained, pre-visible, and has no required owner. Account-cap spillover MUST be decided during account selection and MUST NOT switch an account after a request enters shared transport, replay, or durable bridge ownership. Hard-continuity work MUST remain on its required owner and MAY fail closed when that owner is saturated. Hard Codex ownership rows MUST bypass soft sticky fallback/reallocation so pressure cannot delete or rewrite them.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a self-contained `/v1/responses` request has only bare process-session affinity to account A
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

#### Scenario: Late WebSocket cap race does not retire shared work

- **GIVEN** a request has entered an upstream WebSocket shared with another in-flight response
- **WHEN** a later account response-create lease acquisition loses a capacity race
- **THEN** the proxy rejects only the newly unadmitted request with the existing local-cap failure
- **AND** it does not retire or switch the shared upstream WebSocket to spill that request

#### Scenario: Existing bridge ownership is not replaced by cap spillover

- **GIVEN** a session header resolves to a live or durable HTTP bridge owner
- **WHEN** that owner's account or response-create gate is saturated
- **THEN** the request follows the existing hard bridge-capacity behavior
- **AND** account-cap spillover does not publish a replacement bridge under the same canonical identity

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason fields in logs and metrics so operators can distinguish `bridge_queue_full`, `response_create_gate_timeout`, `hard_affinity_saturated`, `previous_response_owner_unavailable`, `global_admission_timeout`, `capacity_exhausted_active_sessions`, `account_response_create_cap`, and `account_stream_cap`. These local reasons MUST NOT be reported as upstream rate limits.

#### Scenario: Bridge queue saturation is not ambiguous

- **WHEN** a local HTTP bridge queue rejects a request
- **THEN** logs and metrics use the stable reason `bridge_queue_full`
- **AND** they do not use the ambiguous alias `queue_full`

#### Scenario: Queued bridge requests wait for the response-create gate within the request budget

- **WHEN** a visible HTTP bridge request has already claimed a bridge queue slot
- **AND** the per-session `response_create_gate` is held by legitimate in-flight work
- **THEN** each gate acquisition attempt waits until the configured `proxy_admission_wait_timeout_seconds` elapses
- **AND** expired attempts re-enter a recoverable capacity wait bounded by the bridge request budget instead of failing terminally
- **AND** `response_create_gate_timeout` remains the stable reason when the budget is exhausted
- **AND** `bridge_queue_full` remains the bounded local-overload reason when the bridge queue itself is saturated

#### Scenario: Account cap rejection is local overload

- **WHEN** every eligible account is unavailable because of account-local caps
- **THEN** the HTTP response is a local overload response with `Retry-After`
- **AND** logs and metrics identify `account_response_create_cap` or `account_stream_cap`

### Requirement: HTTP bridge startup admission waits are bounded

The proxy MUST apply the configured proxy admission wait timeout to each HTTP bridge startup wait attempt for per-session response-create gate acquisition, bridge capacity waiters, and in-flight session creation waiters.

For per-session response-create gate acquisition by a bridged Responses request, an expired gate acquisition attempt MUST be treated as a recoverable capacity wait rather than a terminal failure: the request MUST release its queue slot and account lease, wait with capacity-wait progress semantics, and retry gate acquisition, bounded by the bridge request budget. Requests eligible for soft-affinity reroute MUST still attempt the reroute before entering the recoverable wait. When the bridge request budget is exhausted before the gate opens, the proxy MUST reject the request locally with HTTP 429, `error.code = "response_create_gate_timeout"`, and the stable local-overload reason.

For bridge capacity waiters and in-flight session creation waiters, when the timeout expires the proxy MUST reject the request locally with HTTP 429 and an OpenAI-style `proxy_overloaded` error envelope. Timing out while observing another request's pending in-flight session creation MUST evict that in-flight marker when it is still pending so later requests can attempt a fresh bridge session instead of waiting on the same stalled future.

If a request owns in-flight bridge session creation and is cancelled or fails after publishing the in-flight marker but before registering the created session, the proxy MUST remove or settle that in-flight marker. If a session owner later finishes creation after its in-flight marker was evicted, the owner MUST NOT return an unregistered bridge session to the caller.

#### Scenario: Gate contention queues within the bridge request budget

- **GIVEN** an HTTP bridge session whose response-create gate is held by a legitimate in-flight turn
- **AND** a bridged Responses request that cannot soft-reroute (hard-affinity key or `previous_response_id` continuity)
- **WHEN** a gate acquisition attempt exceeds the configured proxy admission wait timeout
- **THEN** the request emits capacity-wait keepalive progress on streaming surfaces and retries gate acquisition
- **AND** the request completes normally once the in-flight turn releases the gate before the bridge request budget expires

#### Scenario: Gate contention still fails once the request budget is exhausted

- **WHEN** a bridged Responses request retries response-create gate acquisition until the bridge request budget is exhausted
- **THEN** the request is rejected locally with HTTP 429
- **AND** the error payload uses `error.code = "response_create_gate_timeout"`
- **AND** no response-create gate lease is recorded on that request state

#### Scenario: Soft-affinity requests reroute before waiting

- **GIVEN** a bridged Responses request with a soft-affinity session key and no `previous_response_id`
- **WHEN** its first gate acquisition attempt times out
- **THEN** the proxy attempts the internal soft-affinity reroute to a fresh bridge session
- **AND** the recoverable gate wait applies only when reroute is not permitted

#### Scenario: Stuck sessions are still detected between attempts

- **WHEN** a gate acquisition attempt times out while a pending bridge request has been stuck past the stuck-gate retirement threshold
- **THEN** the stuck session retirement check still runs on that attempt

#### Scenario: In-flight bridge session creation does not finish

- **WHEN** a bridged Responses request waits on another request's in-flight session creation
- **AND** the in-flight creation does not finish before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`
- **AND** the stalled in-flight marker is evicted if it is still pending

#### Scenario: Bridge capacity waiter does not make progress

- **WHEN** the HTTP bridge is at capacity and a request waits for in-flight bridge work to free capacity
- **AND** no capacity becomes available before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`

#### Scenario: In-flight owner is cancelled during stale session close

- **WHEN** a bridge session creation owner has published an in-flight marker
- **AND** it is cancelled while closing a stale local bridge session before creating the replacement session
- **THEN** the in-flight marker is removed or settled
- **AND** later requests do not remain blocked on that cancelled owner's future

### Requirement: Opportunistic Proxy Traffic Burns Only Safe Quota

When a proxy request is authenticated by an API key whose `traffic_class` is `opportunistic`, the proxy SHALL admit the request only if at least one eligible account can serve opportunistic traffic without crossing the routing policy floors.

Burn-first and normal accounts MAY be drained to zero only when another usable foreground account remains. The last usable normal account SHALL keep an emergency reserve. Preserve accounts SHALL require fresh usage data and SHALL remain above dynamic weekly and 5h floors.

#### Scenario: Closed burn window returns OpenAI rate limit
- **WHEN** an opportunistic API key calls a protected Codex-compatible route and no account is currently burnable
- **THEN** the proxy returns HTTP `429`
- **AND** the response uses an OpenAI-style error with code `rate_limit_exceeded`
- **AND** the message begins with `opportunistic burn window closed:`
- **AND** the response includes `Retry-After`

#### Scenario: Preflight admission mirrors routing
- **WHEN** an opportunistic API key calls `/backend-api/codex/opportunistic/admission`
- **THEN** the proxy returns `200` only when the same traffic class could select an account for a real request
- **AND** otherwise returns the same OpenAI-style `429` denial shape

### Requirement: Additional Quota Routing Policies Inherit Or Override Account Policy

When a model is mapped to an additional quota, the proxy SHALL use fresh additional-quota availability as the routing gate and SHALL NOT reject an account solely because its standard 5h or 7d Codex quota is exhausted.

Additional quota routing policy `inherit` SHALL use the selected account's routing policy. Additional quota routing policies `burn_first`, `normal`, and `preserve` SHALL override account routing policy for requests gated by that additional quota.

The dashboard SHALL expose the configured routing policy for each known additional quota and allow operators to switch between `inherit`, `burn_first`, `normal`, and `preserve`.

#### Scenario: Spark can burn its separate pool
- **GIVEN** an account has fresh available `codex_spark` additional quota
- **AND** the account's standard Codex quota is exhausted
- **WHEN** a request selects `gpt-5.3-codex-spark`
- **THEN** the proxy MAY select that account

### Requirement: Stuck HTTP bridge response-create gate sessions are retired

The proxy MUST retain the existing waiter-triggered retirement behavior for stale HTTP bridge response-create gate owners and MUST additionally enforce an owner-side deadline for a visible HTTP request whose current upstream `response.create` send remains completely eventless before `response.created`. The owner-side deadline MUST be measured from a monotonic timestamp recorded immediately before the current upstream send, MUST use the smaller of the configured stuck-gate retirement threshold and 240 seconds, MUST run without a second gate waiter, and MUST remain active when periodic SSE keepalives are disabled.

The owner-side watchdog MUST apply only while the request owns the response-create gate, awaits `response.created`, has neither a response id nor recorded `response.created` latency, has received no matched `response.*` lifecycle event, and has produced no downstream-visible output or sequence evidence. Non-response telemetry such as `codex.rate_limits` MUST NOT suppress this watchdog. Any matched `response.*` lifecycle event, response-created milestone, or downstream-visible evidence MUST suppress the owner-side watchdog and leave existing timeout behavior unchanged.

When the owner-side deadline expires, the proxy MUST recheck eligibility, emit a structured low-cardinality log and the existing stuck-retirement Prometheus counter, terminally fail and settle every pending request exactly once, and retire the whole bridge session. It MUST NOT transparently replay the timed-out request, move it to another account, or write an account-health failure for the missing-created timeout.

If the expired owner used a proxy-injected `previous_response_id` and the bridge owns a durable session row, the proxy MUST conditionally clear that row's automatic latest-response anchor and pending-tool metadata before releasing durable ownership. It MUST retain the latest input count and input fingerprint as recovery proof. The quarantine MUST be one compare-and-set mutation conditioned on the same session id, owner instance, owner epoch, and expected latest response id. A changed owner, changed epoch, concurrently advanced response id, client-supplied anchor, or nonmatching durable anchor MUST remain unchanged. The quarantine write MUST be bounded to no more than five seconds; a persistence error or timeout MUST be logged and MUST NOT prevent terminal settlement or durable release.

If the exact anchor has no usable positive input count and non-empty fingerprint, that same compare-and-set mutation MUST write a reserved negative count and deterministic non-empty fingerprint so the row remains detectably quarantined and cannot be mistaken for fresh continuity. Existing usable proof MUST remain unchanged, the reserved proof MUST never satisfy a prefix match, and a later normal completed response MUST replace it with real input proof.

Proxy-injected anchor provenance MUST survive cross-replica owner forwarding. The forwarding context and reserved header MUST carry the provenance boolean, the canonical structured HMAC payload MUST bind it, and a forward that claims proxy-injected provenance MUST NOT fall back to a legacy signature that does not bind the field. Adding, stripping, or changing the marker MUST either preserve a valid structured signature or reject the forwarded request.

When an owner-forward attempt fails before yielding and the request is re-prepared for a local session, proxy-injected provenance MUST survive only when the re-prepared `previous_response_id` is exactly equal to the original id.

When an upstream HTTP bridge WebSocket disconnects, the proxy MUST proactively quarantine an exact connection-local anchor when either an actually sent request used that anchor with effective `store=false` and proxy-injected provenance or the session's latest completed response was produced with effective `store=false` on that same socket. The proxy MUST record current-socket `store` provenance when a response completes and MUST clear that provenance whenever the upstream socket changes. Sent-request candidates MUST be selected from non-draining HTTP requests under the pending lock, MUST prefer a unique current gate owner or an exact session-latest anchor, and MUST act on a single distinct anchor only when no stronger candidate exists. An exact current-socket latest response MAY be selected when no sent request remains pending. The proxy MUST snapshot the response id and apply the quarantine before an existing safe no-anchor replay may mutate request provenance. When the compare-and-set confirms that exact durable anchor was cleared, the proxy MUST clear the same in-memory latest-response id, its current-socket provenance, and pending-tool metadata while retaining in-memory input count and fingerprint. A CAS miss, persistence failure, fenced owner, or newer durable response MUST NOT clear the in-memory continuity fields. The already-authorized safe replay MAY still run, and a later completed response MUST replace quarantine with its new anchor. Queued or unsent request anchors, `store=true` or unknown-provenance latest responses, client-supplied request anchors, and multiple ambiguous sent anchors MUST remain unchanged. The same owner/epoch/expected-response compare-and-set MUST protect concurrent durable progress. This invalidation MUST NOT add a replay path, move accounts, or add an account-health write.

When a hard-continuity request resolves a durable automatic latest-response id and supplies neither an explicit client `previous_response_id` nor `conversation`, an unanchored incremental request MAY proceed only through a forwardable live owner or a reusable local bridge session whose matching latest response completed with `store=false` on its current socket. The existence of a live local session without that socket-local completion MUST NOT authorize incremental submission. When no such live path exists, the proxy MUST treat the durable id as belonging to a previous socket and MUST NOT inject it. The proxy MAY submit the request unanchored only when the retained count and fingerprint plus the quarantine recovery predicates prove the supplied input is a self-contained full-history resend. Incremental, prefix-mismatched, or otherwise unverifiable input MUST fail closed with the full-resend-required error defined below before upstream transport creation or submission. A refreshed durable lookup after an owner-forward failure MUST reapply the same quarantine and full-resend admission before local takeover. A matching live current-socket response, a forwardable owner, an explicit client-supplied `previous_response_id`, and an explicit `conversation` remain governed by their existing paths.

Immediately after response-create gate acquisition and any closed-session recovery, the proxy MUST revalidate a proxy-injected anchor against the session's current-socket latest response id and `store=false` provenance while lifecycle ownership still excludes socket replacement. If the match was lost during admission, the proxy MUST NOT append or send the serialized anchored request. It MAY instead submit the captured unanchored request only when existing replay-safety proof marks that full request safe; otherwise it MUST fail closed with the full-resend-required error before submission. When HTTP bridge external-image inlining is enabled, both the anchored request and its captured unanchored fallback MUST undergo the same image inlining and surviving-URL validation before this final selection, and each transformed candidate MUST be checked against the upstream serialized request-size budget. When durable lookup replaces a different in-memory latest response id, the proxy MUST clear the previous id's current-socket `store` provenance and pending-tool metadata before the replacement id can influence automatic injection.

When a quarantined automatic anchor, a retained automatic anchor on a fresh socket, or a final send-boundary lineage mismatch cannot pass the verified full-resend predicate, the proxy MUST return HTTP 400 with `error.code` equal to `continuity_requires_full_resend`, `error.type` equal to `invalid_request_error`, and `error.param` equal to `input`. The stable message MUST instruct the client to resend complete conversation context in `input` or create a new session and MUST NOT claim that an upstream WebSocket just closed. Repeating the same incremental request MUST produce the same local error without creating or submitting an upstream transport and without writing account health. Owner lookup, active-owner availability, transport, raw previous-response recovery, and other general continuity failures MUST retain their existing retryable contracts.

#### Scenario: Old pending work blocks a visible gate waiter

- **WHEN** a visible HTTP bridge request receives `response_create_gate_timeout`
- **AND** at least one visible pending request on the same session is older than the configured stuck-gate retirement threshold
- **THEN** the proxy retires the bridge session so later requests can create a fresh session
- **AND** the waiter is rejected cleanly with `response_create_gate_timeout`

#### Scenario: Healthy active stream is not retired during a normal wait

- **WHEN** a visible HTTP bridge request times out waiting for the gate
- **AND** the session has no pending visible request older than the configured stuck-gate retirement threshold
- **THEN** the proxy rejects only the waiter
- **AND** the bridge session remains available for the existing in-flight request

#### Scenario: Lone eventless gate owner is retired before the client timeout

- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** its current `response.create` send produced no matched `response.*` event, response id, or downstream-visible output
- **AND** no second request waits for the gate
- **WHEN** the smaller of the configured stuck threshold and 240 seconds elapses after the current send
- **THEN** the proxy emits an explicit terminal failure and retires the bridge session
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

#### Scenario: Response lifecycle evidence suppresses the narrow watchdog

- **GIVEN** a pre-created request receives any matched `response.*` lifecycle event, a response id, recorded `response.created` latency, or downstream-visible output
- **WHEN** the eventless owner-side deadline would otherwise elapse
- **THEN** this watchdog does not retire the session
- **AND** existing stream, request-budget, and waiter-triggered timeout behavior remains authoritative

#### Scenario: Timeout is fail-closed and account-neutral

- **GIVEN** an eventless pre-created owner reaches the owner-side deadline
- **WHEN** terminal cleanup runs
- **THEN** every pending request is settled exactly once and the whole session is retired
- **AND** the proxy does not replay the timed-out request or submit it on another account
- **AND** the selected account is not marked unhealthy solely because `response.created` was missing

#### Scenario: Eventless proxy-injected anchor is quarantined

- **GIVEN** an eventless pre-created owner reaches the owner-side deadline
- **AND** its `previous_response_id` was injected by the proxy from the durable session's current latest-response anchor
- **WHEN** terminal retirement runs
- **THEN** the proxy clears that exact durable latest-response anchor and pending-tool metadata using owner-and-epoch fencing
- **AND** it retains the input count and fingerprint as quarantine recovery proof
- **AND** it does not replay the failed request without the anchor

#### Scenario: Cross-replica owner preserves injected-anchor provenance

- **GIVEN** an origin replica injects a durable `previous_response_id`
- **AND** forwards the request to the active owner replica
- **WHEN** the owner authenticates the internal forwarding context
- **THEN** it marks the owner-side request state as using a proxy-injected anchor
- **AND** an eventless deadline on that owner invokes the same fenced quarantine path

#### Scenario: Forwarded provenance cannot be downgraded

- **GIVEN** a signed owner forward carries proxy-injected anchor provenance
- **WHEN** the provenance header is added, stripped, or changed in transit
- **THEN** signature verification rejects the request
- **AND** verification does not fall back to a legacy signature that omits the provenance field

#### Scenario: Quarantine persistence does not block settlement

- **GIVEN** an eventless proxy-injected anchor reaches its deadline
- **AND** the durable quarantine write errors or exceeds five seconds
- **WHEN** terminal cleanup runs
- **THEN** the proxy logs the persistence outcome
- **AND** settles every pending request and releases durable ownership without waiting longer

#### Scenario: Concurrent durable progress survives stale quarantine

- **GIVEN** an eventless request was sent with proxy-injected anchor `resp_old`
- **AND** durable ownership changed or the durable latest-response anchor advanced after that send
- **WHEN** the stale request reaches the owner-side deadline
- **THEN** the conditional quarantine mutates no durable continuity fields
- **AND** the newer owner or response anchor remains available

#### Scenario: Explicit client anchor is not quarantined

- **GIVEN** an eventless pre-created owner carries a client-supplied `previous_response_id`
- **WHEN** the owner-side deadline expires
- **THEN** the proxy retires and settles the ambiguous bridge session
- **AND** it does not clear durable latest-response state solely because the explicit client anchor timed out

#### Scenario: Missing historical proof remains fail closed after quarantine

- **GIVEN** the exact proxy-injected anchor has no usable positive input count and fingerprint
- **WHEN** the fenced quarantine clears that anchor
- **THEN** the same mutation stores a reserved non-matching proof pair
- **AND** durable lookup continues to identify the row as quarantined
- **AND** no incoming prefix can match the reserved proof

#### Scenario: Same-anchor local rebind preserves provenance

- **GIVEN** an owner-forward request used a proxy-injected anchor
- **AND** forwarding fails before yielding
- **WHEN** local recovery prepares a new request state with the same `previous_response_id`
- **THEN** the new state retains proxy-injected provenance
- **BUT WHEN** local recovery removes or changes the id
- **THEN** the new state does not inherit that provenance

#### Scenario: Closed store-false socket quarantines its sent automatic anchor

- **GIVEN** a sent HTTP bridge request used a proxy-injected `previous_response_id` with `store=false`
- **AND** the upstream socket disconnects before a terminal response
- **WHEN** the disconnect failure is settled
- **THEN** the proxy quarantines that exact anchor before durable release and before mutable replay preparation can erase the id
- **AND** a confirmed clear removes the matching in-memory latest anchor and pending-tool metadata but retains input proof
- **AND** an independently proven safe no-anchor replay may still run
- **AND** the change does not add an ambiguous replay, move accounts, or add an account-health write

#### Scenario: Disconnect CAS miss preserves in-memory continuity

- **GIVEN** disconnect invalidation selected an old connection-local anchor
- **AND** the durable owner changed, a newer response advanced, or the persistence write failed
- **WHEN** the conditional quarantine does not confirm a clear
- **THEN** the proxy does not clear the local session's latest-response or pending-tool fields
- **AND** normal fenced-owner or disconnect settlement remains authoritative

#### Scenario: Disconnect invalidation ignores unsafe candidates

- **GIVEN** an upstream socket disconnects
- **WHEN** an anchor belongs only to a queued unsent request, is client-supplied, has `store=true`, or conflicts with multiple ambiguous sent anchors
- **THEN** proactive disconnect invalidation does not clear that anchor
- **AND** the normal disconnect settlement remains unchanged

#### Scenario: Idle disconnect quarantines the current-socket latest response

- **GIVEN** an HTTP bridge response completed with effective `store=false`
- **AND** its response id remains the session's durable and in-memory latest anchor
- **AND** no request remains pending
- **WHEN** that same upstream socket disconnects
- **THEN** the proxy conditionally quarantines the exact latest response id
- **AND** a confirmed clear removes its in-memory current-socket provenance and pending-tool metadata while retaining input proof

#### Scenario: Unknown latest-response provenance is not cleared

- **GIVEN** a session carries a durable latest response id loaded from another socket or process
- **AND** no sent pending request proves that id was used on the current socket
- **WHEN** the current socket disconnects
- **THEN** disconnect handling does not claim the id was produced on that socket
- **AND** the fresh-socket recovery guard remains responsible for preventing automatic reinjection

#### Scenario: Fresh socket rejects an incremental store-false reattach

- **GIVEN** a hard durable session retains an automatic latest response id
- **AND** no reusable local socket or forwardable live owner exists
- **WHEN** the client sends incremental or unverifiable input without an explicit anchor
- **THEN** the proxy returns HTTP 400 with code `continuity_requires_full_resend` and parameter `input`
- **AND** the message requests complete context or a new session rather than claiming an upstream close
- **AND** it does not inject the retained connection-local id into the fresh socket
- **AND** it does not create an upstream transport or write account health

#### Scenario: Owner-forward refresh cannot bypass quarantine

- **GIVEN** a hard-continuity request was forwarded using an earlier durable owner lookup
- **AND** the owner forward fails before producing output
- **WHEN** the refreshed durable lookup has no latest response id but retains quarantine input proof
- **AND** the request is incremental or otherwise unverifiable without explicit response or conversation continuity
- **THEN** the proxy returns the full-resend-required HTTP 400 before local session creation or submission

#### Scenario: Live recovery socket without a completed anchor rejects incremental input

- **GIVEN** a hard durable session retains an automatic latest response id
- **AND** a live local recovery socket exists but has not completed that response id on its current socket
- **WHEN** the client sends incremental input without explicit response or conversation continuity
- **THEN** the proxy returns the full-resend-required HTTP 400 before upstream submission
- **BUT WHEN** the client sends a fingerprint-verified self-contained full-history resend
- **THEN** the proxy may submit it unanchored on the recovery socket

#### Scenario: Gate waiter revalidates a connection-local anchor before send

- **GIVEN** a request serialized a proxy-injected anchor that completed with `store=false` on the current socket
- **AND** the request waits for the response-create gate before it is appended or sent
- **WHEN** that socket is replaced and the request later acquires the gate
- **THEN** the proxy does not send the serialized anchor on the replacement socket
- **AND** it sends a captured unanchored full-history request only when existing replay-safety proof authorizes it
- **AND** an anchor-dependent request receives the full-resend-required HTTP 400 before upstream submission

#### Scenario: Stale-anchor fallback preserves image preparation

- **GIVEN** an HTTP bridge request and its replay-safe unanchored fallback contain an external input-image URL
- **AND** bridge image inlining is enabled
- **WHEN** socket replacement invalidates the proxy-injected anchor before send
- **THEN** the selected unanchored fallback contains the inlined image instead of the external URL
- **AND** surviving external URLs fail locally
- **AND** the transformed fallback is rejected locally if it exceeds the upstream serialized request-size budget

#### Scenario: A different durable id does not inherit socket provenance

- **GIVEN** a live session records response `resp_local` with current-socket `store=false` provenance
- **WHEN** refreshed durable metadata replaces it with a different id `resp_durable`
- **THEN** the proxy clears the old socket provenance and pending-tool metadata
- **AND** it does not treat `resp_durable` as completed on the current socket

### Requirement: Account stream capacity reserves recovery headroom

The proxy MUST reserve the configured number of account-local stream slots from ordinary first-turn and follow-up selection, while allowing reattach work to use the full account stream cap. The default recovery reserve MUST be one slot. The reserve MUST NOT increase the configured hard stream cap.

#### Scenario: Fan-out leaves one slot for reattach

- **GIVEN** an account stream cap of eight and a recovery reserve of one
- **AND** seven ordinary streams are active
- **WHEN** another ordinary stream and a reattach stream compete for capacity
- **THEN** the ordinary stream receives local account-cap backpressure
- **AND** the reattach stream may acquire the eighth slot

### Requirement: Dashboard-configurable account concurrency caps

The dashboard settings API MUST persist nonnegative per-account `proxy_account_response_create_limit`, `proxy_account_stream_limit`, and `proxy_account_stream_recovery_reserve` overrides. A settings row created for the first time MUST persist the process environment values for those settings. Existing settings rows upgraded to this capability MUST use nullable overrides so a NULL value continues to inherit the corresponding process environment value until explicitly changed by an operator.

#### Scenario: Operator changes caps without restart

- **GIVEN** the dashboard cache contains persisted account concurrency caps
- **WHEN** an operator updates one or more cap values through `PUT /api/settings`
- **THEN** the response returns the persisted values
- **AND** subsequent new selection and lease decisions use the updated cached values without mutating global process settings

#### Scenario: Negative cap is rejected

- **WHEN** an operator supplies a negative account concurrency cap or recovery reserve
- **THEN** the settings API rejects the request
- **AND** the previously persisted values remain unchanged

#### Scenario: Operator edits caps in the dashboard

- **GIVEN** an operator opens routing settings
- **WHEN** the operator enters nonnegative integer cap values and saves them
- **THEN** the dashboard sends all three values through the settings API
- **AND** `0` is presented as unlimited
- **AND** a bounded stream recovery reserve greater than the stream cap is rejected before saving

### Requirement: Cached caps govern runtime admission

New account selection, account lease acquisition, opportunistic admission, and account-cap error reporting MUST use one dashboard-settings cache snapshot obtained before entering runtime locks. These paths MUST NOT read the database or await the dashboard settings cache while holding a runtime lock.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment stream cap differs from the persisted dashboard stream cap
- **WHEN** a new stream selection or lease acquisition occurs
- **THEN** the persisted cached dashboard cap controls the decision

### Requirement: Stream recovery reserve remains a selection reserve

The configured stream recovery reserve MUST remain a subtractive reserve for ordinary stream selection. Recovery selection without an ordinary reserve MAY use the full stream cap. A nonpositive stream cap continues to mean unlimited streams.

#### Scenario: Recovery may use a reserved slot

- **GIVEN** ordinary stream selection has consumed the configured ordinary capacity
- **WHEN** recovery stream selection is attempted without an ordinary reserve
- **THEN** it may acquire a remaining slot up to the configured stream cap

### Requirement: The fill_first routing strategy MUST select the highest-usage eligible account deterministically

The load balancer MUST pick a single account from the effective candidate
pool by selecting the highest primary 5h `used_percent` when the configured
`routing_strategy` is `fill_first`, treating an unknown `used_percent` as
`0.0`.

When two or more candidates share the same primary `used_percent`, the
balancer MUST prefer the candidate with the **higher** secondary
(weekly) `used_percent` — i.e. the one with the least remaining weekly
capacity — so the most-saturated account is drained first and the
freshest account is preserved for later cycles. An unknown
`secondary_used_percent` MUST be treated as `0.0` for this comparison.
`account_id` ascending MUST be the final stable tiebreaker.

The strategy MUST NOT use randomness. For a fixed snapshot of account
states and clock value, repeated invocations MUST return the same
account.

The strategy MUST reuse the existing effective candidate pool (preferring
healthy accounts, then probing, then draining, falling back to all
available accounts only when no higher-tier candidate exists). It MUST
NOT bypass error backoff, rate-limit cooldown, quota-exceeded cooldown,
or any other availability gate enforced by `select_account`.

When `prefer_earlier_reset` is enabled, `fill_first` MUST narrow the
candidate pool to accounts whose secondary reset bucket is earliest
before applying the highest-`used_percent` ranking, mirroring the
`capacity_weighted` strategy.

#### Scenario: Highest primary usage wins

- **GIVEN** the routing strategy is `fill_first`
- **AND** all eligible accounts share `health_tier = HEALTHY`
- **AND** account `A` has primary `used_percent = 30.0`,
  account `B` has primary `used_percent = 5.0`,
  and account `C` has primary `used_percent = 0.0`
- **WHEN** an account is selected
- **THEN** account `A` is returned

#### Scenario: Stable selection across consecutive calls

- **GIVEN** the routing strategy is `fill_first`
- **AND** the eligible pool and clock are unchanged between calls
- **WHEN** the balancer is invoked repeatedly
- **THEN** the same account is returned every time

#### Scenario: Selection moves on when the current pick leaves the pool

- **GIVEN** the routing strategy is `fill_first`
- **AND** the previously selected account becomes `RATE_LIMITED`,
  `QUOTA_EXCEEDED`, enters cooldown, or transitions to `DRAINING`
  while at least one other healthy account remains
- **WHEN** the balancer is invoked
- **THEN** the next-highest-`used_percent` healthy account is returned
- **AND** no random draw influences the outcome

#### Scenario: Highest secondary usage breaks primary ties

- **GIVEN** the routing strategy is `fill_first`
- **AND** three eligible accounts share primary `used_percent = 99.0`
- **AND** account `alpha` has secondary `used_percent = 29.0`,
  account `bravo` has secondary `used_percent = 98.0`,
  and account `charlie` has secondary `used_percent = 93.0`
- **WHEN** an account is selected
- **THEN** account `bravo` is returned

#### Scenario: Tiebreak by account id when both windows tie

- **GIVEN** the routing strategy is `fill_first`
- **AND** two eligible accounts share the same primary `used_percent`
- **AND** they also share the same secondary `used_percent`
- **WHEN** the balancer is invoked
- **THEN** the account with the lexicographically smaller `account_id`
  is returned

### Requirement: Account concurrency caps are partitioned across live replicas

Each replica MUST derive its local share of every configured account concurrency cap deterministically from the sorted active bridge-ring member list: with `R` active members and this replica at rank `k` in instance-id order, the share MUST be `floor(cap / R)` plus one extra slot when `k < cap mod R`, floored at one slot so an account never becomes unroutable on a replica; a nonpositive configured cap MUST remain unlimited on every replica. Partition derivation MUST NOT add database reads to the request or admission path; it MUST refresh from bridge-ring registration and heartbeat ticks, and the observing replica MUST count itself even when its own ring row is missing or stale. Membership changes that cannot grow this replica's share of any cap MUST be adopted on the next refresh; membership changes that could grow this replica's share MUST NOT be adopted until that exact pending partition (member count and rank) has been observed continuously for the configured stability window. Whether a change could grow the share MUST be decided by comparing the prospective share against the current share for each configured cap (the response-create and stream limits actually in effect — the dashboard-configured overrides when present and otherwise the startup defaults, i.e. the same effective caps the admission path partitions, never the startup defaults when a dashboard override differs) using the same share formula the admission path enforces, and MUST NOT be decided from the direction of the member count or the rank alone: neither direction determines growth, because a member-count decrease can be outweighed by a rank increase and a rank decrease by a large enough member-count increase. A change MUST be deferred only when some configured cap's prospective share is strictly greater than its current share; a change whose every configured cap's prospective share is less than or equal to its current share MUST be adopted on the next refresh, whether the member count or rank rose or fell (for example a member-count decrease paired with a rank increase that reduces this replica's configured share, as when churn removes members while adding lower-sorting instance ids, MUST be adopted immediately rather than held). The stability window (`proxy_account_cap_partition_scale_down_seconds`, default 60 seconds, minimum 30) applies to deferred share-growing changes only; a change of the pending partition, including a rank change at an unchanged count, MUST restart the window. A failed membership read MUST retain the last adopted partition; while a share-growing change is pending, a failed read MUST also restart the stability window so the observation gap does not count toward the continuous-stable requirement. Setting `proxy_account_caps_scope` to `replica` MUST restore per-replica cap semantics, and a replica that observes no other active member MUST use the full configured caps.

#### Scenario: Shares sum to the configured cap

- **GIVEN** a configured account stream cap of 8
- **AND** three active replicas in the bridge ring
- **WHEN** each replica derives its share
- **THEN** the shares by ascending instance-id rank are 3, 3, and 2

#### Scenario: Cap smaller than the replica count keeps accounts routable

- **GIVEN** a configured account response-create cap of 2
- **AND** three active replicas
- **WHEN** each replica derives its share
- **THEN** every replica's share is at least 1

#### Scenario: Scale-up is adopted immediately

- **GIVEN** a replica whose adopted partition has replica count 2
- **WHEN** a refresh observes three active members
- **THEN** the replica adopts the three-way partition on that refresh

#### Scenario: A missed heartbeat does not inflate surviving shares

- **GIVEN** two active replicas and a scale-down stability window of 60 seconds
- **WHEN** one replica's heartbeat goes stale and recovers within the window
- **THEN** the surviving replica keeps its two-way share throughout
- **AND** the two-way partition is only replaced after the lower count is observed continuously for the full window

#### Scenario: Same-count churn does not grow a share early

- **GIVEN** three active replicas with this replica at rank 2 (cap 8 share is 2 slots) and a scale-down stability window of 60 seconds
- **WHEN** the other two replicas drain while later-sorting instance ids appear, keeping the member count at 3 but moving this replica to rank 0 so its cap-8 share would grow from 2 to 3
- **THEN** this replica keeps its previous rank's share until the churned membership has been observed continuously for the full window
- **AND** same-count churn that moves this replica to a later rank (shrinking every configured cap's share) is adopted on that refresh

#### Scenario: Mixed churn that grows the count but moves the rank earlier is deferred

- **GIVEN** a replica whose adopted partition is five members at rank 4 and a stability window of 60 seconds
- **WHEN** a refresh observes six members with this replica at rank 0
- **THEN** the replica keeps its adopted partition until the six-member rank-0 observation has been held continuously for the full window

#### Scenario: Count growth that shrinks the share is adopted immediately despite an earlier rank

- **GIVEN** a replica whose adopted partition is two members at rank 1
- **WHEN** a refresh observes three members with this replica at rank 0 (a rolling replacement where the lower-ranked member drains while two later-sorting ids appear)
- **AND** every configured cap's prospective share is no larger than the current share (for cap 8 the share drops from 4 to 3)
- **THEN** the replica adopts the three-member rank-0 partition on that refresh without waiting for the stability window

#### Scenario: Count decrease that shrinks the configured share is adopted immediately

- **GIVEN** a replica whose adopted partition is six members at rank 0 (cap 8 share is 2 slots)
- **WHEN** a refresh observes five members with this replica at rank 3 (churn removes members while adding lower-sorting instance ids)
- **AND** every configured cap's prospective share is no larger than the current share (for cap 8 the share drops from 2 to 1)
- **THEN** the replica adopts the five-member rank-3 partition on that refresh without holding the larger share for the stability window

#### Scenario: A changed pending target restarts the stability window

- **GIVEN** a replica that has held a share-growing pending partition for part of the stability window
- **WHEN** a refresh observes a different share-growing partition, such as an earlier rank at the same member count
- **THEN** the stability window restarts for the new pending partition
- **AND** the new partition is adopted only after it has been observed continuously for the full window

#### Scenario: Hysteresis gates on the dashboard-configured effective caps

- **GIVEN** a startup stream cap of 8 and a dashboard-configured stream cap of 19
- **AND** a replica whose adopted partition is five members at rank 0 (cap-19 share is 4 slots)
- **WHEN** a refresh observes four members with this replica at rank 2 (no growth for cap 8, but the cap-19 share grows from 4 to 5)
- **THEN** the replica holds its previous partition until the change has been observed continuously for the full stability window
- **AND** the decision uses the dashboard-configured caps, not the startup defaults, so it agrees with the caps the admission path partitions

#### Scenario: Failed membership read retains the partition

- **GIVEN** a replica with an adopted two-way partition
- **WHEN** a partition refresh fails to read ring membership
- **THEN** the replica keeps the two-way partition
- **AND** it does not fall open to the full configured caps

#### Scenario: Failed membership read restarts a pending share-increase window

- **GIVEN** a replica with a share-growing partition pending part-way through the stability window
- **WHEN** a partition refresh fails to read ring membership
- **THEN** the pending stability window is restarted
- **AND** the share-growing partition is adopted only after being observed continuously for the full window from the next successful read

#### Scenario: Replica scope restores legacy semantics

- **GIVEN** `proxy_account_caps_scope` is `replica`
- **AND** two active replicas
- **WHEN** a replica computes its effective account caps
- **THEN** it uses the full configured caps without partitioning

#### Scenario: Partitioned cap rejection states the replica share

- **GIVEN** two active replicas partitioning a configured stream cap of 8
- **WHEN** a request is rejected because the replica's stream share is exhausted
- **THEN** the local overload message states the replica's share, the configured per-account limit, and the replica count
- **AND** the stable reason remains `account_stream_cap`

### Requirement: Multiple worker processes per instance are rejected for shared per-account caps

Per-account concurrency caps are partitioned per bridge-ring replica and are correct only when a single worker process runs behind each bridge-ring instance id. The system MUST expose `workers_per_instance` (env `CODEX_LB_WORKERS_PER_INSTANCE`, default 1, minimum 1) as an explicit operator declaration of how many worker processes an instance runs behind one instance id. When `workers_per_instance` is greater than 1 the process MUST fail fast at startup with a settings validation error that names `CODEX_LB_WORKERS_PER_INSTANCE` and states that running more than one worker per instance is not supported for shared per-account caps and that operators MUST run one worker per pod/container and scale horizontally via replicas. When `workers_per_instance` is 1 (the default) startup MUST proceed with no operator action required and behavior MUST be identical to a deployment that does not set the variable. The system MUST NOT attempt to auto-detect the worker count and MUST NOT partition per-account caps across intra-pod worker processes.

#### Scenario: A single worker per instance is accepted

- **GIVEN** `workers_per_instance` is 1 (the default, whether unset or explicitly set)
- **WHEN** the process loads its settings at startup
- **THEN** startup succeeds and per-account caps remain partitioned per replica via the bridge ring

#### Scenario: More than one worker per instance fails fast

- **GIVEN** `workers_per_instance` is configured as 2
- **WHEN** the process loads its settings at startup
- **THEN** startup fails with a settings validation error naming `CODEX_LB_WORKERS_PER_INSTANCE`
- **AND** the error states multi-worker-per-instance is not supported and directs the operator to run one worker per pod/container and scale via replicas
