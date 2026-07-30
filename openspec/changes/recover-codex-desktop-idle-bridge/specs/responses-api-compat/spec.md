## MODIFIED Requirements

### Requirement: HTTP bridge streams emit downstream liveness frames while pending

When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error.

If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. Before a response id exists, a verified native Codex client on `/backend-api/codex/responses` MUST receive an event-bearing `codex.keepalive` JSON SSE frame even when payload-shape heuristics also require OpenAI-compatible response normalization, because comment-only frames do not reset the native client's parsed-event idle timer. Native identity MUST come from the existing native User-Agent or originator allowlist and MUST NOT be inferred from continuity headers.

Explicit OpenAI SDK fingerprint markers, including `x-stainless-*` headers or an OpenAI User-Agent, MUST retain precedence for heartbeat framing and MUST receive comment liveness. Public `/v1/responses` and other non-native OpenAI SDK streams MUST retain comment heartbeats before `response.created`; public stream normalization MUST preserve those comments and MUST drop `codex.*` liveness events from the OpenAI contract surface. Heartbeat selection MUST NOT disable authentication, payload validation, event normalization, fingerprint normalization, or routing policy.

#### Scenario: Native Desktop shape receives parsed-event liveness

- **GIVEN** Codex Desktop sends `POST /backend-api/codex/responses` with a verified native User-Agent or originator
- **AND** its OpenAI-compatible payload and `Accept` header also trigger SDK-compatible event normalization
- **WHEN** no upstream event arrives before a response id is known
- **THEN** the proxy emits an event-bearing `codex.keepalive` JSON SSE frame
- **AND** it preserves any required response-event normalization

#### Scenario: Explicit SDK marker retains comment liveness

- **GIVEN** a request to `/backend-api/codex/responses` carries an `x-stainless-*` header or OpenAI User-Agent
- **WHEN** its payload also resembles a native Codex request
- **THEN** the proxy emits an SSE comment heartbeat before `response.created`
- **AND** it does not expose `codex.*` vendor events to the SDK stream

#### Scenario: Public v1 route never exposes native vendor heartbeat

- **GIVEN** a request targets public `/v1/responses`
- **WHEN** the request is pending before `response.created`
- **THEN** periodic liveness uses OpenAI-contract-safe comment frames
- **AND** the first data event remains `response.created`

#### Scenario: First HTTP bridge keepalive is delayed past startup probe

- **GIVEN** an HTTP bridge request is waiting for upstream queue events
- **AND** `sse_keepalive_interval_seconds` is shorter than the bridge startup-error probe window
- **WHEN** no upstream event arrives before the configured keepalive interval
- **THEN** the first generated keepalive is not emitted until the startup-error probe window has elapsed
- **AND** a startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response before any keepalive commits the stream

#### Scenario: HTTP bridge keepalive commits stream for later response-failed events

- **GIVEN** an HTTP bridge request emits a generated keepalive as its first downstream chunk
- **WHEN** the next upstream event is a `response.failed` with an HTTP status override
- **THEN** the `response.failed` event is forwarded on the SSE stream
- **AND** it is not raised as a startup HTTP error after bytes have already been emitted

#### Scenario: Public Responses normalizer preserves comment keepalive blocks

- **WHEN** the public `/v1/responses` stream normalizer receives an SSE comment keepalive block before a terminal event
- **THEN** it forwards the comment keepalive block unchanged
- **AND** it continues normalizing the subsequent Responses events normally

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before signaling failure for those pending requests, except when the close carries a classified process-wide network failure. A classified process-wide network failure MUST remain account neutral and use its network error code.

A Responses receive failure without a complete peer close frame MUST be classified as a process-wide network failure when a bounded process-local correlation window observes at least two distinct non-empty upstream account ids fail on the same concrete egress within one second. All candidates in that incident MUST be classified before health settlement as `proxy_network_unavailable`. An owned receive task that has entered bounded correlation MUST complete that decision before a request-budget, stream-idle, or eventless-response deadline can settle the failure. Same-account repeats, different concrete egresses, anonymous accounts, explicit close frames, and live sideband sockets MUST NOT satisfy this correlation rule and MUST retain existing account-health behavior. Correlation MUST NOT make the interrupted post-dispatch request replayable or permit continuity to move across accounts.

For other closes, the proxy MUST surface `stream_incomplete` to affected pending requests except when a direct Responses WebSocket request has already successfully emitted a finite integer `sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST record the request outcome as `stream_incomplete` without emitting a synthetic terminal frame under the active response id, then MUST close the downstream WebSocket with code 1011.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: sequenced direct websocket closes before completion

- **GIVEN** a direct Responses WebSocket request has successfully emitted a finite integer `sequence_number`
- **WHEN** the upstream websocket closes before a terminal response event is observed
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: correlated no-close failures remain account neutral

- **GIVEN** pending Responses requests for at least two distinct upstream accounts use the same concrete egress
- **WHEN** their receive paths fail without complete peer close frames within one second
- **THEN** every correlated request fails with `proxy_network_unavailable`
- **AND** no correlated account receives a transient failure signal
- **AND** no request is replayed or moved to another account

#### Scenario: Deadline settlement waits for observed no-close classification

- **GIVEN** a direct WebSocket or HTTP bridge receive task has entered bounded no-close correlation
- **WHEN** its request or idle deadline expires before cross-account evidence arrives
- **THEN** the bounded receive classification completes before terminal settlement
- **AND** a correlated network failure is not replaced by a timeout or account-health penalty

#### Scenario: explicit and uncorrelated closes preserve health behavior

- **WHEN** a receive failure names only one account, uses a different concrete egress, or carries an explicit close frame
- **THEN** bounded cross-account no-close correlation does not apply
- **AND** the existing close classification and account-health behavior remain authoritative

## ADDED Requirements

### Requirement: Eventless durable reattach anchors do not loop forever

For a hard-continuity HTTP bridge request, a durable `latest_response_id` MAY be injected automatically into a fresh upstream session only while that durable anchor remains trusted. If a request carrying that proxy-injected anchor reaches the eventless missing-`response.created` deadline, the service MUST quarantine the exact durable latest anchor through the fenced compare-and-set behavior defined by proxy admission control. It MUST NOT replay the timed-out request as an anchorless fresh turn.

Because a `store=false` Responses WebSocket anchor is connection-local, a non-text upstream disconnect or a downstream SSE cancellation that retires the current upstream socket MUST also make an actually sent proxy-injected anchor or an exact latest response proven to have completed on that socket ineligible for later automatic injection. The service MUST apply the protected disconnect-quarantine selection, fenced mutation, and confirmed in-memory matching-anchor clear defined by proxy admission control before durable release and before existing safe no-anchor replay or cancellation retirement may mutate request provenance. It MUST NOT infer current-socket provenance from an unsent request, a client-supplied id, a `store=true` request, a durable id loaded from another socket or process, ambiguous sent anchors, or a failed/fenced persistence mutation. An already-proven safe full-context replay MAY continue without the quarantined anchor, but a canceled request MUST NOT be replayed.

A hard-continuity request without explicit `previous_response_id` or `conversation` MUST NOT automatically inject a retained `store=false` durable latest-response id onto a fresh WebSocket. A live local session MUST count as a usable continuity path only when the durable latest id matches a response completed with `store=false` on that session's current socket; a live recovery socket without that completion MUST apply the same full-history admission as a fresh socket. The durable row otherwise remains available only for owner routing and full-history recovery proof. A fingerprint-verified self-contained full-context resend MAY start a new unanchored lineage; incremental, prefix-mismatched, or otherwise unverifiable input MUST fail with the full-resend-required client error before an upstream transport is created or a request is submitted. A refreshed lookup after owner-forward failure MUST reapply quarantine admission before local takeover. An explicit client anchor or `conversation` remains distinct from automatic injection.

An upstream-issued encrypted `compaction` item MAY replace plaintext fingerprint matching only for the same hard-continuity durable fresh-socket or quarantined-anchor recovery. The request MUST omit explicit `previous_response_id` and `conversation`; durable state MUST retain a positive input count, non-empty fingerprint, and concrete owner account; and selection MUST stay fixed to that account. The first input item MUST contain exactly non-blank `id`, literal `type: "compaction"`, and non-blank `encrypted_content`. Remaining input and request controls MUST satisfy the existing account-neutral self-contained fresh-replay validator. The service MUST forward an admitted compaction item unchanged and without the old automatic anchor. It MUST NOT admit malformed items, arbitrary summaries, account-scoped suffix state, missing durable proof or owner, soft-affinity use, ordinary incremental requests, or any cross-account compaction replay.

If a request waits for response-create admission after a proxy-injected connection-local anchor is serialized, the service MUST revalidate that anchor against current-socket `store=false` completion provenance immediately before enqueue and send. A socket replacement or mismatched durable id MUST NOT carry the serialized anchor across the WebSocket boundary. The service MAY switch to the captured unanchored request only when existing replay-safety proof already marks that full request safe; an anchor-dependent request MUST fail with the full-resend-required client error before upstream submission. When HTTP bridge external-image inlining is enabled, the anchored and captured unanchored candidates MUST both retain that preparation, surviving external-image URLs MUST fail locally, and the serialized size guard MUST apply after transformation to whichever candidate may be sent.

A later self-contained full-context client resend MUST remain unanchored when durable lookup observes the quarantined state, so it can establish a new upstream response lineage. The quarantined state MUST be derived without a redundant schema field from an absent latest response id together with a retained input count and fingerprint. Before unanchored recovery, the service MUST verify the stored prefix fingerprint and either the existing completed-response safe-full-resend evidence or a quarantine-only self-contained mid-tool continuation. The mid-tool alternative MUST require the projected entire input to have a self-contained call/output graph. The suffix after the projected stored boundary MUST independently satisfy the account-neutral fresh-input validator and MUST contain at least one complete supported direct tool-call/output pair. It MUST NOT require a later assistant-final or new user message. The retained prefix MAY contain existing owner-bound tool declarations only while account selection remains fixed to the durable owner; this alternative MUST NOT make that prefix eligible for account movement. A request without an explicit anchor that is incremental, prefix-mismatched, contains an orphan or incomplete tool call, has unsupported or account-scoped state in its new suffix, or is otherwise not proven self-contained MUST fail closed with the full-resend-required client error before creating or forwarding an upstream request. This alternative MUST NOT relax generic or cross-account replay policy. Historical response-id aliases MAY remain available for explicit client continuity and owner resolution.

The full-resend-required client error MUST use HTTP 400 with `error.code` equal to `continuity_requires_full_resend`, `error.type` equal to `invalid_request_error`, and `error.param` equal to `input`. Its stable message MUST ask the client to resend complete conversation context in `input` or create a new session and MUST NOT report an upstream WebSocket close. Repeated identical incremental requests against the same quarantined or stale lineage MUST return that same error before transport creation or submission and MUST NOT write account health. Potentially recoverable owner, transport, raw previous-response, and general continuity failures retain their existing retryable error contracts.

#### Scenario: Full-context resend recovers after eventless durable reattach

- **GIVEN** a fresh HTTP bridge socket times out before any `response.*` event while using a proxy-injected durable anchor
- **AND** the exact anchor is quarantined successfully
- **WHEN** the client later resends self-contained full context without `previous_response_id`
- **THEN** the proxy verifies the retained input count and fingerprint plus safe-full-resend evidence
- **AND** does not re-inject the quarantined response id
- **AND** the full-context request is forwarded as an unanchored fresh response

#### Scenario: Completed mid-tool full-history resend recovers after quarantine

- **GIVEN** durable lookup identifies a quarantined latest-response anchor with retained input count and fingerprint
- **AND** the later unanchored request matches that stored prefix
- **WHEN** the projected suffix contains a complete supported direct tool call and its matching output
- **AND** the projected entire input has a self-contained call/output graph
- **AND** the projected suffix independently satisfies account-neutral fresh-input validation
- **THEN** the proxy forwards the full-context request as an unanchored fresh response
- **AND** recovery does not require a later assistant-final or user message
- **AND** generic and cross-account replay eligibility remain unchanged

#### Scenario: Malformed mid-tool resend after quarantine fails closed

- **GIVEN** durable lookup identifies a quarantined latest-response anchor
- **WHEN** a purported full-context resend has a mismatched prefix, orphan tool output, unresolved tool call, duplicate call id, or unsupported/account-scoped state in the new suffix
- **THEN** the proxy returns the full-resend-required HTTP 400
- **AND** it does not create, forward, or submit an unanchored upstream request

#### Scenario: Incremental request after quarantine fails closed

- **GIVEN** durable lookup identifies a quarantined latest-response anchor
- **WHEN** the client sends incremental or prefix-mismatched input without an explicit `previous_response_id`
- **THEN** the proxy returns HTTP 400 with code `continuity_requires_full_resend`, type `invalid_request_error`, and parameter `input`
- **AND** it does not create, forward, or submit an unanchored upstream request

#### Scenario: Repeated Goal continuation remains a deterministic client error

- **GIVEN** an automatic durable anchor is quarantined or belongs to a prior socket
- **WHEN** a Goal client repeatedly submits the same incremental continuation without complete context
- **THEN** every attempt returns the same full-resend-required HTTP 400
- **AND** the message requests complete context or a new session without claiming another upstream close
- **AND** no attempt creates an upstream transport or writes account health

#### Scenario: Explicit conversation remains independent of automatic-anchor quarantine

- **GIVEN** durable lookup identifies quarantined automatic response-anchor state
- **WHEN** the client supplies an explicit `conversation` without `previous_response_id`
- **THEN** the proxy does not reject the request solely because the automatic anchor is quarantined
- **AND** the existing explicit-conversation continuity path remains authoritative

#### Scenario: Owner-forward refresh observes newly quarantined state

- **GIVEN** an origin forwards a hard-continuity request using an earlier trusted owner lookup
- **WHEN** the forward fails before output and the refreshed lookup has an absent latest response id with retained input proof
- **AND** the request is incremental or unverifiable without explicit response or conversation continuity
- **THEN** the proxy returns the full-resend-required HTTP 400 before local session creation or submission

#### Scenario: Live recovery socket requires socket-local anchor provenance

- **GIVEN** a live local recovery socket exists for a hard durable session
- **AND** that socket has not completed the durable latest response id with `store=false`
- **WHEN** the client sends incremental input without explicit response or conversation continuity
- **THEN** the proxy returns the full-resend-required HTTP 400 before submission
- **BUT WHEN** the client sends a fingerprint-verified self-contained full-history resend
- **THEN** the proxy may submit it unanchored on that socket

#### Scenario: Socket replacement during gate wait cannot carry an automatic anchor

- **GIVEN** a request serialized an automatic `store=false` anchor from the current WebSocket
- **AND** it waits for response-create admission
- **WHEN** the WebSocket lineage changes before the request is sent
- **THEN** the proxy does not submit the serialized anchor on the replacement socket
- **AND** a replay-safe full-history request may proceed without the anchor
- **AND** an anchor-dependent request receives the full-resend-required HTTP 400 before submission

#### Scenario: Image-bearing full-history fallback remains upstream-safe

- **GIVEN** a replay-safe full-history request has anchored and unanchored forms containing an external input-image URL
- **AND** HTTP bridge image inlining is enabled
- **WHEN** a socket replacement selects the unanchored form at the final send boundary
- **THEN** the selected frame retains the inlined image and contains no surviving external URL
- **AND** the selected transformed frame remains subject to the upstream serialized request-size guard

#### Scenario: Timed-out incremental request is not replayed fresh

- **GIVEN** a request depends on a proxy-injected durable anchor and is not independently self-contained
- **WHEN** it reaches the eventless missing-`response.created` deadline
- **THEN** the proxy returns the explicit terminal failure
- **AND** it does not retry that request without `previous_response_id`

#### Scenario: First full-history retry after a store-false disconnect avoids the dead anchor

- **GIVEN** an upstream WebSocket disconnects after a sent HTTP bridge request used a proxy-injected `store=false` anchor
- **WHEN** the client retries with verified self-contained full context
- **THEN** the closed socket's exact automatic anchor has already been quarantined
- **AND** the retry follows the existing unanchored quarantine-recovery guard
- **AND** it does not first wait for another missing-`response.created` deadline on the dead connection-local id

#### Scenario: Escape interruption permits the next verified same-session turn

- **GIVEN** the client cancels a downstream Codex SSE stream before its upstream response completes
- **AND** retiring the old socket conditionally quarantines its exact eligible automatic `store=false` anchor
- **WHEN** the client sends the next turn under the same session header with fingerprint-matched self-contained full history
- **THEN** the proxy opens a fresh upstream socket and submits that history without the quarantined anchor
- **AND** it does not reconnect or replay the interrupted request

#### Scenario: Automatic encrypted compaction survives a fresh socket

- **GIVEN** Codex replaces previously fingerprinted history with an upstream-issued encrypted `compaction` item
- **AND** durable hard-continuity state retains the original owner and prior input proof but no reusable socket
- **WHEN** Codex continues the same session without an explicit anchor
- **THEN** the service opens a fresh socket on that owner and forwards the compaction item unchanged without `previous_response_id`
- **AND** it does not classify the opaque context replacement as an incremental plaintext request

#### Scenario: Idle close still invalidates the latest connection-local response

- **GIVEN** the latest response completed with `store=false` on the current WebSocket
- **AND** the socket closes while no request is pending
- **WHEN** the client later sends verified self-contained full history
- **THEN** the completed response id has already been conditionally quarantined
- **AND** the retry starts a new unanchored lineage without a dead-anchor timeout

#### Scenario: Process restart never reattaches a store-false durable id

- **GIVEN** a process starts with a hard durable row whose latest response came from a previous WebSocket
- **AND** no live owner can receive the request on that socket
- **WHEN** the client sends a verified self-contained full-history request without an explicit anchor
- **THEN** the proxy forwards the request unanchored on the fresh socket
- **BUT WHEN** the client sends incremental or unverifiable input
- **THEN** the proxy returns the full-resend-required HTTP 400 before opening the upstream transport

#### Scenario: Soft prompt-cache reconnect does not inherit hard continuity failure

- **GIVEN** a soft prompt-cache bridge row has quarantined its closed socket's automatic `store=false` anchor
- **AND** the next request supplies no explicit `previous_response_id`
- **WHEN** a self-contained request reaches a fresh upstream socket
- **THEN** the proxy creates the soft-locality session without the quarantined id
- **AND** it does not apply the hard-continuity full-history guard solely because the soft row retains quarantine proof
