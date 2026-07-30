## Context

The HTTP Responses bridge serializes upstream `response.create` submissions with a per-session gate and releases that gate when `response.created` is observed. Current `main` can retire a stale pre-created gate owner when another request later times out waiting for the gate, but it has no owner-side deadline. A lone request can therefore remain pending indefinitely after a successful WebSocket send produces no matched `response.*` event.

The production request that motivated this change was eventless after its current `response.create` send and remained pending for 3,467 seconds. Codex Desktop disconnected first because its parsed-event idle timeout is 300 seconds. The backend route had classified the native request as OpenAI-SDK-shaped from its payload and `Accept` header, so periodic SSE comments never reached the parsed-event timer.

After the owner-side watchdog shipped, the same long-running session exposed a durable recovery loop. Each client retry created a fresh upstream socket, but durable reattach injected the last completed response id into the full-context resend. The new socket again produced no lifecycle event, the watchdog retired it after 240 seconds, and durable release left the same anchor available for the next retry.

After quarantine was added, the observed Codex CLI retry still failed closed. Its stored 71-item prefix matched the durable fingerprint exactly, while the projected suffix contained assistant commentary, a complete `custom_tool_call` / `custom_tool_call_output` pair, and later developer/user messages. The generic full-resend predicate requires a later completed assistant message followed by fresh user input unless a durable pending-call manifest exists. Quarantine intentionally clears that manifest, so the valid mid-tool full-history resend was misclassified as incremental.

Production aggregation later found ten client-visible disconnect failures across seven conversations and three upstream accounts. In the later five conversations, a `stream_incomplete` on one upstream socket was immediately followed by one `missing_response_created_timeout` on the same conversation and account. Each active conversation later succeeded and none repeated the missing-created timeout, which shows that quarantine stops the loop but currently acts one request too late. The local process did not restart during those later incidents; observed upstream close modes included no close frame, 1000, 1001, and 1012.

At 19:32:10 in a later incident, one shared environment-proxy EOF terminated seven Responses WebSockets across four accounts in 358 milliseconds. One owner account had three concurrent requests, so three ordinary `stream_incomplete` health writes crossed the load balancer's transient-error threshold even though its credentials, quota, and persisted status remained healthy. Continuity-bound retries then received `previous_response_owner_unavailable` until the local backoff expired. The request that first exposed the 502 arrived later and was not the trigger.

A subsequent Goal-enabled session showed that the local fail-closed contract could itself create a retry storm. After one real WebSocket failure quarantined the old anchor, roughly 57 automatic incremental continuations were rejected by `quarantined_anchor_requires_full_resend`. Those rejections created no upstream transport, yet each reused the retryable `stream_incomplete` 502 and the message "Upstream websocket closed before response.completed". Goal interpreted the response as another transient disconnect and kept retrying. A fresh Goal session worked, so the repeated failures required a client action—complete context or a new session—not another automatic retry.

The upstream protocol explains the sequence. OpenAI documents that a Responses WebSocket handles one in-flight response, currently tops out at 60 minutes, and keeps the most recent response warm in a connection-local cache. Its reconnect guidance requires `store=false` or otherwise unresolvable chains to restart with `previous_response_id` omitted and full input context. Re-injecting a durable id from the closed socket into a fresh socket therefore cannot be treated as durable continuity.

Current `main` already provides terminal request settlement, whole-session retirement, a stuck-retirement Prometheus counter, lifecycle locking, native Codex identity detection, durable continuity coordination, and safe later-waiter recovery. This change reuses those primitives directly and does not import PR #1394's retry circuit, replay policy, new schema, or migration surface.

## Goals / Non-Goals

**Goals:**

- Terminate an eventless request that remains pre-`response.created` before the native client's 300-second idle boundary, without requiring another gate waiter.
- Measure the deadline from the current upstream send rather than request construction or admission wait.
- Fail closed through existing settlement and session-retirement paths without replaying ambiguous work or moving it to another account.
- Prevent a proxy-injected durable anchor that reaches the eventless deadline from being re-injected indefinitely, without erasing a newer concurrent anchor.
- Preserve proxy-injected anchor provenance when the request is forwarded to another bridge owner.
- Reject a post-quarantine incremental request instead of silently submitting it as a fresh turn.
- Recover a fingerprint-matched Codex full-history resend that advances through a complete, self-contained direct tool-call/output round trip, even before a later assistant-final or user message exists.
- Invalidate a sent proxy-injected `store=false` anchor as soon as its upstream socket is known closed, before any existing safe no-anchor replay can mutate the request state, so a later full-history retry does not spend another 240 seconds on the dead connection-local id.
- Keep quarantined durable rows fail-closed even when historical input proof is absent, and retain injected-anchor provenance through a same-anchor local rebind.
- Prevent one process-local, shared-egress Responses WebSocket EOF from being counted as independent account failures while preserving ordinary one-account circuit-breaker signals.
- Stop deterministic full-resend-required guards from masquerading as retryable upstream disconnects.
- Send parser-visible liveness to verified native Codex clients while retaining OpenAI event normalization when their payload needs it.
- Preserve structured low-cardinality retirement metrics and logs.

**Non-Goals:**

- Replay a pre-visible request, retry clean upstream closes, or persist retry cooldowns across replicas.
- Hide or transparently replay the request interrupted by the upstream disconnect after response lifecycle or downstream-visible evidence; without an upstream idempotency/resume contract, that could duplicate model or tool side effects.
- Recover a request after any matched `response.*` lifecycle event; eventful missing-created recovery remains outside this narrow change.
- Change account selection, continuity ownership, request budgets, public `/v1/responses`, or operator settings.
- Replay the timed-out request without its anchor or turn an incremental continuation into an automatic fresh turn; anchorless recovery is left to a later self-contained client resend.
- Relax generic or cross-account replay rules, accept account-scoped/unsupported state in the newly appended suffix, move an owner-bound retained prefix to another account, or infer safety from an orphaned or incomplete tool item.
- Correlate disconnect incidents across replicas, suppress failures with complete close frames, or make post-dispatch transport failures replayable.
- Change owner-unavailable, ordinary network, raw previous-response lookup, or other potentially recoverable continuity errors from their existing retryable contracts.
- Merge PR #1394, deploy the result, or alter the current Mac mini runtime as part of this code change.

## Decisions

### 1. Measure from the actual current send

Add one optional monotonic `response_create_sent_at` field to the in-memory request state. Set it immediately before each actual upstream `send_text` of the current `response.create`. A later send replaces the timestamp, so admission and queue time from an earlier attempt cannot make a fresh send expire immediately.

This timestamp is protocol evidence, not a general request timer. Request start time is not an acceptable fallback for the proactive watchdog because a request may legitimately spend most of its budget waiting for admission before it reaches upstream.

### 2. Use the existing threshold with a client-safe cap

The proactive window is `min(http_responses_session_bridge_stuck_gate_retire_after_seconds, 240 seconds)`. This introduces no new setting, preserves deliberately shorter existing thresholds, and leaves at least 60 seconds before the native client's 300-second parsed-event timeout.

The upstream-reader wait uses the earliest applicable deadline. The watchdog remains active when SSE keepalives are disabled because downstream liveness and upstream acceptance are separate concerns.

### 3. Keep the eligibility deliberately narrow

The proactive timeout applies only while the current HTTP request:

- owns the response-create gate and is awaiting `response.created`;
- has a current send timestamp;
- has no response id or recorded `response.created` latency;
- has no matched `response.*` lifecycle event;
- has no downstream-visible output or sequence evidence.

Leading non-response telemetry such as `codex.rate_limits` does not change those conditions. Any matched response lifecycle event protects the request from this narrow watchdog, even if it later becomes stale; handling that ambiguous state safely requires broader sibling and replay coordination and is intentionally deferred.

### 4. Fail the whole bridge session closed

When the deadline expires, reuse the reader-owned terminal failure and whole-session retirement path. Emit a stable `missing_response_created_timeout` detail, increment the existing stuck-retirement metric, settle every pending request exactly once, and close the bridge session.

Do not transparently replay the timed-out request, submit it on another account, or mark the selected account unhealthy. Upstream acceptance is unknown, so duplicate submission and account movement are less safe than an explicit terminal failure. A later client request creates a fresh session through existing behavior.

Example: a request sends at monotonic time 1,000 with the default 300-second stuck threshold. With no matched response lifecycle event, it becomes eligible at 1,240 and receives an explicit terminal failure; it does not wait for a second request or the 300-second Desktop idle timeout.

### 5. Separate heartbeat identity from event normalization

Continue using `_is_openai_sdk_request` to decide whether response events need the OpenAI compatibility normalizer. Separately derive verified native identity from the existing `_is_native_codex_request` allowlist. A native request without explicit SDK fingerprint markers receives `CODEX_KEEPALIVE_FRAME` even if payload-shape heuristics enable normalization.

Explicit `x-stainless-*` headers or an OpenAI User-Agent retain comment liveness. Public `/v1/responses` never enables the native heartbeat override. This changes only liveness framing; it does not relax authentication, routing, payload validation, fingerprint normalization, or vendor-event filtering.

### 6. Quarantine only the exact proxy-injected durable anchor

If the expired eventless owner used a proxy-injected `previous_response_id`, clear the durable row's `latest_response_id` and pending-tool metadata before durable session release. Retain `latest_input_item_count` and `latest_input_full_fingerprint` as the proof needed to distinguish a safe full-context resend from an incremental request. The update is one compare-and-set statement fenced by `(session_id, owner_instance_id, owner_epoch, expected_latest_response_id)`.

The response-id alias remains available for explicit client continuity and owner resolution; quarantine removes only the automatic latest-anchor injection state. A client-supplied `previous_response_id` is never quarantined by this watchdog.

If the durable owner or epoch changed, or another response advanced `latest_response_id`, the update mutates nothing. The newer owner or anchor remains authoritative. A persistence error or the bounded five-second persistence timeout is logged, but it does not prevent terminal settlement of the already ambiguous in-memory session.

The timed-out request is not replayed after quarantine. Durable lookup derives quarantine from `latest_response_id is None` together with a non-null input count and fingerprint; no redundant schema field is added. A later request without an explicit anchor may establish a new upstream response lineage only when the existing prefix/fingerprint and safe-full-resend checks prove that its payload contains the prior output and a fresh follow-up. An incremental, mismatched, or otherwise unverifiable request fails closed with the existing retryable continuity error.

### 7. Bind proxy-injected provenance across owner forwarding

Add a boolean proxy-injected-anchor field to `HTTPBridgeForwardContext` and its reserved internal header. Include it in the canonical structured HMAC payload. When the field is true, the origin uses the versioned structured primary signature as well as the full-body signature, and the receiver does not accept a downgrade to the delimiter-based legacy primary signature.

This makes stripping, adding, or changing the provenance marker fail signature validation. During a mixed-version rollout, an updated origin forwarding a proxy-injected anchor to an owner that cannot authenticate the new context fails closed rather than silently losing quarantine provenance. Requests without proxy-injected provenance retain the existing rolling-upgrade compatibility path.

### 8. Recognize self-contained mid-tool history only during quarantine recovery

Keep the existing completed-assistant-plus-new-user and durable pending-call-manifest checks unchanged. Add one quarantine-only alternative after the durable input count and raw prefix fingerprint have already matched. The projected entire input must have a self-contained call/output graph. The projected suffix after the stored boundary must independently satisfy the strict account-neutral fresh-input validator and contain at least one complete supported direct tool-call/output pair. A later assistant-final message or new user message is not required for this alternative because the paired output itself is the fresh continuation evidence.

This path validates a later client-generated full-history resend; it does not replay the request that timed out. It does not change account selection or enable cross-account replay. The fingerprint-proven retained prefix may contain existing owner-bound `additional_tools` declarations because it remains on the durable owner account; it is not required to become cross-account portable. Requiring a self-contained whole-history call graph plus independently account-neutral suffix rejects an output that depends on a call before the stored boundary, duplicate call ids, unsupported or account-scoped suffix items, orphan outputs, and calls without outputs. Requiring an actual pair prevents ordinary incremental messages from using this alternative.

### 9. Make quarantine fail closed without historical prefix proof

Some historical durable anchors do not have both a positive input count and a non-empty fingerprint. Clearing such an anchor to null values would make the row indistinguishable from a genuinely fresh session, allowing a later incremental request to start a new lineage without prior context.

The same fenced compare-and-set that clears the exact response id writes a reserved negative input count and deterministic non-empty fingerprint only when usable positive proof is absent or incomplete. Existing positive proof remains unchanged. Durable lookup therefore still derives quarantine from the existing columns without a schema flag, while `_input_prefix_matches_stored_context` rejects the negative count unconditionally. A later normally completed response overwrites the sentinel with real input proof.

A local request state reconstructed after an owner-forward failure also retains proxy-injected provenance only when its `previous_response_id` is exactly the same as the original state. A changed or removed id does not inherit provenance.

### 10. Quarantine connection-local anchors at socket disconnect

Record the validated Responses `store` boolean on each request state. Before attempting the existing pre-created replay after a non-text upstream disconnect, snapshot an eligible anchor candidate under the pending-request lock. Eligibility requires an HTTP request that was actually sent, is not draining, has `store=false`, and carries a proxy-injected non-null `previous_response_id`. Queued requests have no current send timestamp and are excluded.

Also record the effective `store` value for the latest response completed on the current socket. Clear that current-socket provenance whenever the upstream is replaced. Prefer the unique current response-create gate owner, then an eligible anchor matching the session's last completed response id, then a single distinct eligible sent anchor. If no sent candidate exists but the exact latest response is proven to have completed with `store=false` on the disconnecting socket, select that id so an idle close is covered. If multiple different sent anchors remain ambiguous and no current-socket latest id is proven, do nothing.

Snapshot the immutable response id and quarantine it before the existing safe no-anchor replay can clear provenance from mutable request state. When the compare-and-set confirms the exact durable anchor was cleared, clear the same in-memory latest-response id, its current-socket provenance, and pending-tool metadata while retaining input count and fingerprint; this prevents a replay-retained local session from re-injecting the old id. A CAS miss, persistence failure, fenced owner, or newer response leaves in-memory continuity untouched. The replay remains allowed and a later completed response replaces the quarantined state. The owner/epoch/expected-response compare-and-set remains the final authority, so a stale candidate cannot erase a newer anchor.

This invalidates only the automatic reattach optimization. It does not create a new replay path, move accounts, add an account-health write, clear historical aliases, or alter the client-visible `stream_incomplete` for work whose upstream acceptance or side effects are ambiguous. An already-proven safe full-context replay may still run without the anchor. Otherwise Codex's subsequent full-history retry reaches the existing quarantine guard immediately instead of first sending a dead connection-local id and waiting for the eventless watchdog.

### 11. Treat every fresh store-false socket as a new lineage boundary

An idle-close handler cannot run after a process crash or restart. Durable lookup may therefore find a non-null latest response id whose connection-local cache disappeared with the old process. Do not copy or inject that id into a fresh WebSocket merely because the durable row still owns account and prefix metadata.

Before and after local session resolution, distinguish a forwardable live owner and an exact response completed with `store=false` on the resolved session's current socket from a true fresh lineage. A pre-resolution observation that some local session is live is not sufficient: a recovery socket can exist before its first response completes, and a concurrent incremental request must not use that bare socket as continuity proof. For a hard-continuity automatic-anchor request without either authoritative live path, apply the same retained-prefix and safe-full-resend predicates used by quarantine recovery, including the self-contained complete tool-call/output alternative. A verified full-history request remains unanchored. Incremental, mismatched, or unsupported input fails closed before transport creation or submission. Reapply this quarantine admission whenever an owner-forward failure refreshes and replaces the durable lookup, because the anchor may have been quarantined concurrently. These checks apply only to automatic `store=false` reattach; explicit client anchors and explicit `conversation` continuity keep their existing resolution paths.

The durable id remains useful for account routing and prefix proof until a new response completes and overwrites it. Avoid a pre-claim mutation because another live owner or concurrently advanced response may still be authoritative. The absence of current-socket provenance also prevents the newly created session from copying that durable id into its in-memory automatic-anchor slot.

### 12. Revalidate connection-local provenance at the final send boundary

Session resolution is not the final submission boundary. A request can serialize a proxy-injected anchor, then wait behind the response-create gate while the reader observes a disconnect, quarantines the anchor, and replaces the socket. Gate waiters are not yet present in `pending_requests`, so disconnect selection cannot use their mutable request state as evidence.

After gate acquisition and any closed-session recovery, revalidate the request's injected id against the session's latest response id and `store=false` provenance while holding the lifecycle lock that also covers enqueue and send. If the match still holds, preserve the existing anchored path. If it does not, use the captured unanchored request only when the existing replay-safety flag already proves that request is a self-contained full-history fallback; otherwise return the existing retryable continuity failure before appending or sending the request. When external-image inlining is enabled, prepare both serialized candidates before this late choice: inline and validate image URLs independently and apply the upstream size budget to each transformed frame. Preparing only the anchored frame would let a socket change restore raw external URLs from the stale captured fallback. When durable lookup replaces a different in-memory latest response id, clear the old id's socket-local `store` provenance together with pending-tool metadata so that provenance cannot bind the replacement id.

### 13. Correlate ambiguous Responses receive failures before health settlement

Treat only a typed Responses WebSocket receive failure without a complete peer close frame as a correlation candidate. Record the candidate under a credential-safe concrete-egress key: the actual routed proxy endpoint id, the parsed environment-proxy endpoint, or the direct destination. Do not derive identity or classification from exception message text.

Before returning the adapter message to relay settlement, wait for at most one second. Two distinct non-empty upstream account ids on the same egress within that window establish a process-local correlated outage. Notify all waiters from that incident before any can reach account-health settlement, retain a short bounded observation history so trailing candidates in the same window receive the same result, and classify them as `proxy_network_unavailable`. Keep the observation store bounded and cancellation-safe.

The direct Responses relay keeps one owned upstream receive task across downstream keepalive ticks. A keepalive timeout may emit liveness, but it does not cancel and restart an adapter receive that is already making its bounded correlation decision. Request-budget, stream-idle, and eventless-response deadlines likewise defer only when that owned receive task has already entered the bounded classification; its completed transport result reaches settlement before a local timeout can overwrite it. A truly silent receive remains subject to the original deadline. Relay shutdown and terminal settlement still cancel and await that owned task.

Repeated failures from one account, failures on different concrete egresses, anonymous accounts, explicit close frames, and live sideband sockets retain their existing classification. A correlated failure is account neutral but remains post-dispatch and therefore is not replay-safe, does not move continuity ownership, and does not switch accounts. Process-local scope deliberately matches the observed single-replica incident without adding distributed state or a schema migration.

### 14. Make deterministic full-resend admission failures client-actionable

Use one dedicated HTTP bridge error for guards that have already proved an incremental continuation cannot recover on the current lineage: a quarantined automatic anchor without a verified full resend, a retained connection-local anchor on a fresh socket without a verified full resend, or a gate waiter whose proxy-injected anchor lost current-socket provenance and has no already-proven safe unanchored fallback.

Return HTTP 400 with `error.code = "continuity_requires_full_resend"`, `error.type = "invalid_request_error"`, and `error.param = "input"`. The stable message instructs the client to resend the complete conversation context in `input` or create a new session. It does not claim that an upstream WebSocket just closed. Repeating the same incremental Goal request returns the same deterministic error before transport creation and cannot write account health.

Keep the helper narrow and call it only from those guards. Owner lookup failures, active-owner unavailability, network failures, upstream close settlement, raw previous-response recovery, and general continuity loss retain their existing retryable errors because a later identical attempt may recover without changing the request.

## Risks / Trade-offs

- **A send fails after the timestamp is set.** Existing send-error cleanup retires or settles the request before the watchdog can act; tests cover that the timestamp alone is not sufficient eligibility.
- **A quiet upstream accepted the request but emitted no event.** The proxy returns an explicit failure rather than risking a duplicate replay. The selected account remains healthy because silence is not proof of account failure.
- **A matched lifecycle event arrives just before timeout.** Eligibility is rechecked under the existing request/session synchronization before retirement, and any matched `response.*` event suppresses this watchdog.
- **A newer response commits while quarantine is pending.** The conditional response-id predicate preserves the newer anchor and all metadata coupled to it.
- **Durable ownership changes while quarantine is pending.** Owner and epoch fencing makes the stale watchdog write a no-op; the replacement owner remains authoritative.
- **A later request contains only incremental input.** Retained input proof marks the durable row as quarantined, and the request fails before transport creation instead of becoming a fresh turn.
- **A later request stops at a normal tool boundary.** A fingerprint-matched, fully projected history with a complete direct call/output suffix may start a new lineage without waiting for a synthetic user turn.
- **A malformed tool suffix resembles a full resend.** Whole-history and suffix self-containment plus the complete-pair requirement reject orphan outputs, unresolved calls, duplicate ids, and unsupported tool state.
- **A historical anchor has no usable input proof.** The quarantine sentinel remains identifiable but can never satisfy the prefix matcher, so recovery fails closed until a real completed response replaces it.
- **A queued request happens to name the same anchor when a socket closes.** Absence of a current send timestamp excludes it from disconnect invalidation.
- **The socket closes while completely idle.** Current-socket completion provenance permits an exact latest-response quarantine even with an empty pending queue.
- **A process exits without observing the close.** The next fresh socket treats the retained automatic `store=false` id as recovery proof only and never injects it.
- **The socket changes while an anchored request waits for the gate.** Final send-boundary revalidation either removes the stale anchor from an already-proven full-history request or fails the dependent incremental request before upstream submission.
- **The safe unanchored fallback contains an external image.** Both candidates are inlined, validated, and size-checked before final selection, so choosing the fallback cannot restore an upstream-incompatible URL or bypass the frame budget.
- **A durable id is loaded into a new process but its origin is unknown.** Unknown current-socket provenance prevents idle-close inference; the fresh-socket full-context guard, not an unfenced guess, controls recovery.
- **Two accounts independently lose no-close sockets close together.** They may be classified as a shared egress incident, but neither transport failure is credential evidence; keeping both accounts selectable is safer than poisoning continuity owners, and the interrupted requests still fail without replay.
- **One broken account repeatedly drops sockets.** Distinct-account counting prevents its concurrent requests from manufacturing a shared incident, so the existing transient health penalty remains active.
- **Two different proxy endpoints fail together.** Concrete-egress keys keep their observations separate, so each retains ordinary account-health treatment unless its own endpoint has cross-account evidence.
- **The process shuts down during the correlation wait.** Task cancellation interrupts the bounded wait, and stale observations expire from a bounded in-memory store without owning transport resources.
- **A local deadline expires during correlation.** The already-observed transport failure completes its bounded classification first; silent reads that never entered correlation still settle at the normal deadline.
- **A Goal client does not understand the new code.** HTTP 400 stops generic transient retry behavior, while the message still gives two safe recovery actions; the service never converts the incremental request into a fabricated fresh turn.
- **A transient continuity failure resembles the deterministic guard.** Only the three proven full-resend boundaries use the new helper; all other continuity paths retain their current retryable status.
- **Several sent requests name different anchors.** The selector acts only with a unique gate owner, an exact session-latest match, or one distinct candidate; otherwise the fenced durable state is left unchanged.
- **Durable quarantine succeeds while safe replay keeps the local session.** The matching in-memory latest anchor and pending-tool metadata are cleared so the local session cannot undo durable quarantine; input proof remains available.
- **The disconnect CAS misses or persistence fails.** The proxy does not claim in-memory quarantine succeeded and leaves local continuity fields unchanged; normal failure/retry settlement remains authoritative.
- **A persisted response id remains resolvable after reconnect.** Proactive disconnect invalidation requires `store=false`; `store=true` requests retain the existing reconnect behavior.
- **The disconnect interrupted already-visible work.** The proxy still fails that request instead of replaying it; only the dead automatic anchor is quarantined for the client's next self-contained retry.
- **A forwarding hop tampers with or strips provenance.** Versioned structured signatures bind the marker and prevent fallback to an unbound legacy signature.
- **A mixed-version owner cannot verify the new provenance field.** That cross-replica request fails closed during rollout; non-provenance forwards remain backward compatible.
- **Whole-session retirement interrupts a healthy sibling.** This narrow design chooses fail-closed session cleanup rather than attempting unsafe sibling isolation on current `main`. Existing terminal settlement must cover every pending sibling exactly once.
- **A client spoofs native identity.** The only benefit is an ignored vendor liveness event on the authenticated Codex backend route; explicit SDK markers still take precedence.

## Migration Plan

No data migration or setting change is required. Deploying a new process initializes the monotonic field and bounded correlation state in memory and uses existing nullable durable-anchor columns. Rollback restores the previous timeout, durable-anchor retention, heartbeat selection, and per-disconnect health classification without persistent-state conversion.

## Open Questions

None. The scope intentionally removes the observed eventless/no-waiter wedge, the immediately preceding connection-local-anchor reinjection window, and the observed shared-egress health amplification without introducing transparent replay.
