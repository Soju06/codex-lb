## MODIFIED Requirements

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
