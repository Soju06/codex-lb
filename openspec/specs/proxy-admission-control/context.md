# Proxy Admission Control Context

## Purpose and Scope

This capability protects proxy work at global, traffic-class, transport, and account boundaries. It covers where admission decisions happen and how local capacity failures remain distinguishable from upstream rate limits.

See `openspec/specs/proxy-admission-control/spec.md` for normative requirements.

## Account-cap Spillover Decision

Bare process-session affinity is locality, not ownership. When its mapped account is at a response-create or stream cap, selection may use another eligible account for the current self-contained, pre-visible request. The mapping itself is left untouched, so later work returns to the original locality account when capacity recovers.

Persistent rebind was rejected because admission completes at different points in plain streaming, compact, direct WebSocket, and HTTP bridge flows. Moving the mapping would require settlement and compensating rollback across sticky rows, durable bridge rows, local registries, and shared sockets. Request-local spillover removes that distributed transaction.

## Constraints and Failure Modes

- Spillover ends at transport handoff. A late lease race returns the existing bounded local-cap error rather than switching a shared WebSocket or publishing a replacement bridge.
- Previous-response, file, conversation, turn-state, live/durable bridge, replay, and reattach ownership remain fail-closed.
- A single process must run per instance because account caps are partitioned across replicas, not safely across worker processes inside one instance.
- Repeated self-contained requests may use different alternates during sustained pressure; this is an accepted cache-locality trade-off.

## Example

Session `S` is mapped to account A. A has all response-create slots in use, while account B has capacity. A new self-contained request carrying only `S` may run on B, but the stored mapping still points to A. A later request that references a response created on B follows that response's hard owner index; it does not rely on `S`.

## Eventless HTTP Bridge Gate Retirement

The HTTP bridge has both waiter-side and owner-side stuck-gate recovery. Waiter-side recovery handles a later request blocked behind old work; the owner-side watchdog covers the otherwise invisible case where a lone upstream `response.create` send receives no matching response lifecycle event. Its clock begins at the actual send, not at request construction, and is capped at 240 seconds so native clients receive a terminal result before their 300-second parsed-event idle boundary.

The watchdog is intentionally narrower than the general request and stream timeouts. Leading telemetry does not prove that a response was accepted, while any matched `response.*` event, assigned response id, recorded created latency, or downstream-visible output makes the state ambiguous and leaves the existing timeout paths authoritative. Eligible timeouts fail the whole bridge session closed, settle pending work, and remain account-neutral; automatic replay or account movement could duplicate work whose upstream acceptance is unknown.

Durable recovery quarantines only the automatic latest-response anchor used by the timed-out send. One owner-and-epoch-fenced compare-and-set clears the anchor and pending-tool metadata while retaining the input count, input fingerprint, and historical aliases. The retained input proof distinguishes a safe self-contained resend from an incremental request that would otherwise lose context. A replacement owner or newer response anchor wins the race, and a persistence error or five-second write timeout is recorded without preventing terminal in-memory settlement.

Historical rows may lack usable positive input count and fingerprint proof. In that case the same fenced mutation writes a reserved negative count and deterministic non-matching fingerprint instead of nulling all proof. The row therefore remains recognizably quarantined and fails closed until a normally completed response replaces the sentinel with real proof.

Proxy-injected provenance is part of the signed cross-replica owner-forward context. The structured HMAC binds the marker, and a marked request cannot downgrade to the legacy signature shape. This prevents a receiving owner from mistaking an automatically injected anchor for a client-supplied anchor and skipping quarantine.

An upstream socket close is an earlier invalidation point for a `store=false` connection-local anchor. While holding the pending-request lock, the bridge first considers actually sent, non-draining HTTP requests with proxy-injected provenance and an unambiguous anchor. It also remembers the effective `store` value of the latest response completed on the current socket, so an idle close can identify the exact latest anchor even after the pending request has been settled. That current-socket provenance is reset whenever the upstream socket changes; a response id loaded from durable state is therefore never guessed to belong to the new socket. The bridge snapshots the selected response id before mutable replay preparation, applies the same fenced compare-and-set, and clears matching in-memory latest-response, socket-provenance, and pending-tool state only after a confirmed durable clear. Queued requests, client-supplied anchors, persisted or unknown-provenance responses, ambiguous candidates, fenced owners, and newer durable progress remain unchanged.

This early invalidation does not make the interrupted request replayable. The request still follows the existing disconnect settlement because upstream acceptance and tool side effects may be unknown. It only prevents a later verified full-context retry from first reattaching the closed socket's dead automatic anchor and waiting for the eventless watchdog.

Downstream SSE cancellation (for example, Codex Escape) reaches the same result through a different control path: the proxy intentionally cancels its upstream reader before closing the shared socket, so it must snapshot and quarantine the eligible anchor during detach rather than waiting for a peer-close message the reader can no longer observe. The old socket remains an ownership barrier and the canceled request is never replayed.

A subsequent Codex automatic compaction can replace that fingerprinted plaintext history with one upstream-issued encrypted `compaction` item. That opaque item is accepted only as same-account complete context: the durable owner stays fixed, the old connection-local response id stays omitted, and any later items must be self-contained and account neutral. A normal text summary or malformed compaction remains subject to the full-resend-required guard.

A process exit cannot run the idle-close handler, so every truly fresh socket is also a lineage boundary. A retained automatic response id still supplies account-routing and prefix proof, but it is not copied into the new socket. A self-contained full-history resend can start an unanchored lineage after its retained prefix and recovery evidence are verified; an incremental, mismatched, or malformed history fails before transport creation. Reusable local sockets and forwardable live owners keep their current-socket path, while an explicit client-supplied response id remains a separate continuity contract.

Response-create admission is another socket-lineage race boundary. A request can serialize a valid current-socket anchor and then wait behind the session gate while the reader replaces that socket. The final send path therefore rechecks the id and `store=false` provenance under lifecycle ownership: a previously verified full-history fallback drops the stale anchor, while an incremental continuation fails before it is appended or sent. Both candidates receive the same configured external-image inlining before that choice, including surviving-URL validation and a post-transformation serialized-size check. Otherwise the fallback could restore an `https://` image URL that the anchored frame had already converted, causing the upstream WebSocket to reject or hang. Replacing the local latest-response id from durable metadata also clears provenance belonging to the old id.

A Goal-enabled production session made the error boundary operationally important. One real disconnect quarantined its anchor, then roughly 57 automatic incremental continuations hit the local full-resend guard. Those attempts never opened an upstream transport, but the former 502 `stream_incomplete` envelope described each one as another WebSocket close, so Goal kept retrying. Deterministic guard failures now use an actionable invalid-request response that asks for complete `input` context or a new session. Owner, network, and other potentially self-healing continuity failures keep their retryable status.

For example, if session `S` sends at monotonic time 1,000 with durable anchor `resp_old` and produces no response lifecycle event, the default watchdog becomes eligible at 1,240. It returns an explicit failure and conditionally removes `resp_old` from automatic reattach state. The same quarantine can happen immediately if `resp_old` completed with `store=false` and that socket later closes while idle. If the process instead exits before observing the close, the next fresh-socket request still never injects `resp_old`: a self-contained resend can start unanchored only after the retained prefix fingerprint and safe-full-resend checks pass, while an incremental or mismatched resend receives `continuity_requires_full_resend` before upstream transport creation. If that resend contains an external input image and inlining is enabled, both serialized candidates carry the inlined `data:` URL, and the selected unanchored frame is size-checked after conversion. A concurrent advance to `resp_new` is preserved.

## Operational Notes

Operators can distinguish local account pressure through the stable `account_response_create_cap` and `account_stream_cap` reasons. The spillover behavior is zero-config because it mutates no ownership state; rollback restores conservative fail-closed selection without data conversion.

Monitor the existing stuck-gate retirement counter, the structured `missing_response_created_timeout` detail, upstream `stream_incomplete` close modes, `continuity_requires_full_resend` client-action rejections, and durable-anchor quarantine outcomes for HTTP bridge incidents. A `stream_incomplete` followed by one missing-created timeout on the same conversation/account indicates that invalidation happened one request late; after this change an observed close should quarantine before the client retry, while a process-restart retry should reach the fresh-socket full-history guard directly. Repeated full-resend-required 400s mean the client is still sending incremental context and should not be counted as fresh transport failures or account-health evidence. Compare-and-set misses usually mean ownership or the response lineage advanced safely. No new setting or database migration is involved.

Related capabilities: `openspec/specs/sticky-session-operations/` and `openspec/specs/responses-api-compat/`.
