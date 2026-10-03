# Model Source WebSocket

## Purpose

Native, source-bound Responses sessions with per-turn authorization and settlement.

## Requirements

### Requirement: Sources explicitly declare native Responses WebSocket support

Each source SHALL expose a persisted `supports_responses_websocket` boolean through create/read/update and the existing dashboard form. It MUST default to false for old and new sources, MUST require Responses support when true, and MUST survive management round trips. An eligible model MUST also be enabled and support streaming. Capability omission on update MUST preserve its previous value. HTTP forwarding MUST remain unchanged by this capability.

#### Scenario: Migration and partial update preserve existing behavior
- **WHEN** an existing source is migrated and later updated without the WebSocket field
- **THEN** WebSocket support remains disabled and its existing HTTP routes remain usable

#### Scenario: Capability validation checks the resulting source
- **WHEN** an update would leave WebSocket enabled and Responses disabled
- **THEN** the update fails with a management validation error and the stored configuration is unchanged

### Requirement: Source WebSocket routes retain source authorization and payload semantics

Both `/v1/responses` and `/backend-api/codex/responses`, including explicit trailing-slash equivalents without redirect, SHALL support opted-in source requests. Routing MUST apply raw/enforced public-model policy, source assignments, per-source overrides and aliases before dispatch. It MUST preserve source HTTP reasoning/instruction/tool semantics and validate original and effective ownership references. Subscription-owned anchors, uploaded-file ownership, structural compaction exclusions and required-capability security rules MUST retain their precedence. Lookup failure MUST fail closed before reservation or upstream dispatch.

Domain errors raised during shared preparation or source quota reservation MUST retain the existing WebSocket error status, type, code, message and supplied parameter. This contract MUST also apply when the eventual backend is subscription and when no model source is configured.

Shared source ownership preparation MUST preserve the existing HTTP error envelope on both Responses route families and their trailing-slash equivalents. An unavailable recorded source owner MUST return status 409 with type `server_error` and code `previous_response_owner_unavailable` when `previous_response_id` is present, or `model_source_owner_unavailable` for other owned references. This contract MUST apply regardless of the source's WebSocket capability; WebSocket adaptation MUST retain the same ownership-error envelope.

#### Scenario: A recorded source owner becomes unavailable
- **GIVEN** an HTTP source response published a response or output-item reference
- **WHEN** the source is disabled and the client continues using that reference over HTTP or WebSocket
- **THEN** the shared ownership check MUST retain the 409 `server_error` envelope and the reference-appropriate error code
- **AND** the rejected turn MUST NOT reserve quota, acquire source admission or reach any provider

#### Scenario: Shared preparation rejects key policy
- **WHEN** a create on either public WebSocket route or trailing-slash equivalent uses a revoked key or a forbidden model or reasoning effort
- **THEN** it MUST retain the established authentication or permission error type and any parameter such as `reasoning.effort`
- **AND** no rejected request reaches a provider or acquires quota

#### Scenario: Source quota is exhausted
- **WHEN** source quota reservation rejects an initial or subsequent create with a rate-limit domain error
- **THEN** its WebSocket error MUST retain status 429 and `rate_limit_error`, consistent with HTTP Responses
- **AND** the rejected turn MUST release admission without creating a reservation or sending an upstream request

#### Scenario: Source alias reaches the selected provider
- **WHEN** an authorized create requests a source alias over either public WS path
- **THEN** only its selected source receives its mapped upstream model and source credential
- **AND** the client and accounting retain the public model identity

#### Scenario: Overrides cannot bypass file ownership
- **WHEN** a source override introduces an account-scoped file reference
- **THEN** the request is rejected before source reservation or dispatch and is not redirected using stale pre-override state

#### Scenario: Failed source lookup does not select an account
- **WHEN** source routing or ownership lookup fails
- **THEN** the turn receives an error, no subscription/source attempt is made, and no quota reservation remains

#### Scenario: A turn inherits a required capability
- **WHEN** a create carries a session, turn, parent-task or previous-response alias with a durable required capability, including an alias introduced by its effective source payload
- **THEN** capability routing takes precedence before any source admission or reservation
- **AND** unavailable lineage lookup fails closed and no source receives the turn

#### Scenario: A reconnect carries a subscription-owned turn state
- **WHEN** a create carries an explicit turn-state alias owned by a subscription account under the exact API key, including without a previous response ID
- **THEN** the source path MUST preserve subscription ownership or fail closed before source admission
- **AND** conflicting or unavailable ownership lookup MUST NOT permit source dispatch, while an unregistered synthesized source placeholder MUST remain usable

#### Scenario: An invalid first subscription create is corrected
- **WHEN** shared or subscription-specific payload validation rejects the first create before backend preparation succeeds
- **THEN** the socket remains available for a corrected create without becoming bound to either backend

### Requirement: Native source connections isolate endpoint and credentials

The source WebSocket endpoint MUST derive from the configured HTTP(S) Responses endpoint using WS(S). Upstream authentication MUST use only the selected source's configured credential. Downstream credentials, cookies, subscription account and installation headers MUST NOT be forwarded. Handshake redirects MUST NOT be followed. Public errors MUST NOT expose credentials or private upstream connection details.

#### Scenario: A provider redirects its handshake
- **WHEN** the source handshake returns a redirect
- **THEN** no second endpoint is contacted and the attempt ends with a redacted source error

### Requirement: Source sessions implement a bounded sequential Responses profile

A source session SHALL support JSON text `response.create`, normal response events, tool-result continuation and `generate:false`. It MUST process at most one generation at a time, queue at most one later create, and reject excess creates without a reservation. Named `stream_id`, steering and application cancellation events MUST be rejected explicitly without changing an active turn; downstream disconnect SHALL cancel the session. HTTP-only envelope fields MUST NOT be sent upstream, and unsupported background execution MUST be rejected. Malformed, binary or oversized input and slow consumers MUST have bounded error/cleanup behavior. Idle sockets MUST NOT hold inference admission or quota reservations.

Queued work MUST hold no inference admission/reservation, MUST include queue time in its request deadline and MUST revalidate policy and identity at dequeue. Errors for rejected input MUST NOT reference or finalize an unrelated active generation. A successful no-generation warmup MUST settle reported usage when present, or an input estimate with zero estimated output when absent; its reservation MUST use that no-output budget. Estimated usage MUST NOT be recorded as observed upstream usage. Warmup classification MUST use the final effective payload, and unexpected generated output MUST be withheld and treated as an invalid upstream response.

Every source-bound client message MUST use the same maximum of 16 MiB measured from its original UTF-8 JSON text, including the first create. Oversized creates MUST be rejected with `invalid_request_error` before inference admission, quota reservation or provider dispatch. A rejected initial create MUST leave the socket available for corrected input. Subscription-bound messages MUST retain their existing ingress limits.

#### Scenario: The first source create exceeds the native message limit
- **WHEN** a client submits a source create whose original JSON text exceeds 16 MiB on either route or trailing-slash equivalent
- **THEN** it receives `invalid_request_error` without a reservation or upstream call
- **AND** an identical create on an established source socket MUST be rejected by the same limit
- **AND** a subsequent bounded valid create MUST remain usable

#### Scenario: A new turn arrives immediately after completion
- **WHEN** the preceding terminal event has been sent but its settlement is pending
- **THEN** one subsequent create can wait and cannot dispatch before the preceding attempt has finalized

#### Scenario: Warmup has no generated output
- **WHEN** a supported source returns a warmup response for `generate:false`
- **THEN** its returned references and usage follow normal ownership and accounting rules without fabricated generated output

#### Scenario: Warmup omits usage
- **WHEN** a limited-key no-generation warmup succeeds without a usage object
- **THEN** it settles an input estimate and zero estimated output, while its request log does not claim observed usage

#### Scenario: Source is disabled while work waits
- **WHEN** a create is queued and its source is disabled before dequeue
- **THEN** the queued request is denied without a reservation or upstream send and the preceding turn remains independently finalized

#### Scenario: An unsupported control does not interrupt a turn
- **WHEN** the client sends a named lane or steering/cancel event
- **THEN** the control receives an unsupported-operation error and is not forwarded or billed as a new generation

### Requirement: Reused connections preserve source identity and recheck permission

Every create MUST recheck current key policy, source enablement/capability and transport identity. A session MUST NOT reuse its upstream for a changed source, credential revision, public model or upstream model. A required identity or source/subscription backend change MUST return a reconnect-required turn error without forwarding through the existing connection. Already admitted work MUST retain its cleanup/settlement owner.

#### Scenario: A credential rotates between turns
- **WHEN** the source credential changes after a completed turn and another create arrives
- **THEN** the old socket receives no new request and the client receives a reconnect or continuity error as applicable

### Requirement: Source WebSocket turns use the Responses stream request budget

Native source Responses WebSocket turns MUST use
`http_responses_stream_request_budget_seconds` for their total request and
generation deadline when that setting is available. They MUST fall back to
`proxy_request_budget_seconds` only when the stream-specific setting is absent.
The selected source's own timeout MAY shorten that deadline, and connection
retries and queued turns MUST retain the earliest deadline already applied to
the original request.

#### Scenario: A source turn outlives the generic proxy budget
- **GIVEN** `proxy_request_budget_seconds` is 60 seconds
- **AND** `http_responses_stream_request_budget_seconds` is 7200 seconds
- **AND** the selected source timeout is longer than 60 seconds
- **WHEN** an active source generation outlives 60 seconds without exhausting its source or stream deadline
- **THEN** the native source WebSocket remains active and does not emit `model_source_timeout`

#### Scenario: The stream budget is shorter than the generic budget
- **GIVEN** the stream-specific budget is 60 seconds and both the generic proxy budget and source timeout are longer
- **WHEN** an active source turn exceeds 60 seconds
- **THEN** it MUST emit `model_source_timeout` and close with owned cleanup

#### Scenario: A settings provider has no stream-specific budget
- **WHEN** no stream-specific budget is available
- **THEN** the source request deadline MUST use the effective dashboard-overridden generic proxy budget

#### Scenario: Source retry retains the stream deadline
- **GIVEN** a portable source turn has a stream-specific request budget
- **WHEN** its first source fails before sending and preparation selects a replacement source
- **THEN** retry preparation and the replacement generation remain bounded by the original stream deadline

### Requirement: Source ownership is durable before event delivery

All response, item, call, encrypted and hosted-resource references introduced by source output MUST be recorded before their containing event is delivered, within the exact client-key/public-model/source-revision scope. Conflicting, unavailable or invalidated owners MUST fail closed. Changing transport or HA replica MUST NOT authorize another source/credential. Publication failure MUST withhold the affected output, terminate the attempt and prohibit replay. Reconnection MUST NOT silently remove an anchor or reconstruct missing connection-local history.

#### Scenario: A reconnect reaches a different replica
- **WHEN** a continuation uses an ID delivered by a source on another replica
- **THEN** it resolves only to the recorded eligible owner
- **AND** missing upstream state returns a safe continuity error without cross-source retry

#### Scenario: A provider reports a stale response anchor
- **WHEN** the selected provider reports a recognized missing previous response after reconnect
- **THEN** the Codex-native route exposes sanitized `previous_response_not_found` and the public route exposes `stream_incomplete`
- **AND** neither envelope reveals the raw provider message, missing anchor or endpoint

#### Scenario: Ownership publication fails
- **WHEN** persistence fails before an output reference is delivered
- **THEN** that event is withheld, attempt cleanup completes and no other source receives a replay

### Requirement: Source WebSocket attempts preserve settlement and safe retry boundaries

Source admission MUST precede quota reservation. Each attempt MUST settle or release exactly once and produce one source-attributed log. Existing source usage policy MUST apply: successful limited-key responses without usage settle an estimate; cancellation after content delivery follows the existing estimate policy; pre-content cancellation and failure/truncated responses release. A portable initial request MAY try at most five distinct eligible sources only for proven pre-send connection failure or eligible handshake rejection. After send begins, ambiguous delivery MUST NOT cause automatic replay. Owned continuations MUST NOT switch sources. Prior settlement/admission release MUST finish before cooldown writes or another reservation. A source WebSocket MUST retain transport-level keepalive pings without treating a delayed pong as a response-stream failure; first-frame, stream-idle and total-turn deadlines remain authoritative bounds for stalled work.

#### Scenario: Admission changes during effective-policy lookup
- **WHEN** another request fills a portable initial request's selected source while its effective policy is being checked
- **THEN** preparation MUST retry another eligible candidate within the original deadline and revalidate that candidate's effective policy before reservation
- **AND** an owned continuation MUST NOT switch sources

#### Scenario: A reservation heartbeat encounters a transient database error
- **WHEN** a reservation touch fails transiently during an active generation
- **THEN** later heartbeat intervals MUST retry the touch or terminate the generation with owned cleanup
- **AND** a single failed touch MUST NOT silently stop refresh for the rest of the generation

#### Scenario: Disconnect occurs during an uncertain send
- **WHEN** transmission of `response.create` has begun and the upstream disconnects without a response
- **THEN** the attempt is finalized and no source receives an automatic replay

#### Scenario: A limited-key client leaves after receiving content
- **WHEN** the client disconnects after content delivery and usable terminal usage is absent
- **THEN** existing source estimation/settlement applies once and all admission claims are released

#### Scenario: A usage-bearing terminal is withheld before any delivery
- **WHEN** the client disconnects while a successful terminal with reported usage is waiting for ownership publication or a downstream write lock and no content has been handed to the client transport
- **THEN** the native attempt MUST release its reservation without charging the withheld usage
- **AND** a successful terminal already handed to the transport MUST retain successful settlement

#### Scenario: A connection retry selects a source with a longer timeout
- **WHEN** a portable generation retries a proved pre-send connection failure against an eligible source with a longer timeout
- **THEN** its deadline MUST NOT exceed the earliest source/request deadline already applied to that generation

#### Scenario: A disconnect races terminal delivery
- **WHEN** the client disconnects while a known failure terminal is being published or sent
- **THEN** the attempt releases its reservation even if earlier content or usage was observed
- **AND** a successful terminal handed to the downstream transport retains successful settlement, including warmup input estimation, while a terminal withheld by ownership persistence does not count as delivered

#### Scenario: A terminal send times out after delivery
- **WHEN** a terminal has been handed to the downstream transport and that send later times out or disconnects
- **THEN** the observed terminal retains its accounting result and the session closes without emitting a contradictory second terminal error

#### Scenario: Retry preparation stalls
- **WHEN** source preparation stalls after a pre-send connection failure
- **THEN** the original request deadline bounds the retry preparation and timeout does not reset that deadline

#### Scenario: Provider delays pong while response work is active

- **WHEN** an opted-in source delays its WebSocket pong beyond the transport library's default heartbeat interval while response events remain within the configured first-frame, stream-idle and total-turn deadlines
- **THEN** the proxy MUST keep the source connection available for the response and MUST NOT report `model_source_stream_truncated` solely because the pong was delayed

#### Scenario: Response deadlines still bound a silent source

- **WHEN** a source sends no response event within the configured first-frame or stream-idle deadline
- **THEN** the proxy MUST close the source connection and return its existing timeout error with exactly-once settlement

### Requirement: Source transport policy and health are scoped correctly

Explicit global HTTP transport SHALL retain ordinary handshake denial with HTTP 426. Global auto/WebSocket policy MUST NOT enable a source lacking its own capability. A recent subscription WebSocket failure MUST NOT by itself deny a request routed to an authorized capable source. Source errors MUST NOT modify subscription account health or its global transport-failure marker. Incapable-source requests MUST retain the HTTP-required error and MUST NOT be sent to subscription accounts. After upgrade, failures MUST use WebSocket error envelopes rather than claim an HTTP handshake status was returned.

#### Scenario: One transport family fails
- **WHEN** the subscription transport failure marker is active and a permitted opted-in source is requested under auto mode
- **THEN** its source session can connect independently and its failures remain source-scoped

#### Scenario: An enforced subscription model cannot use the source exemption
- **WHEN** the subscription transport failure marker is active and effective key policy cannot select a capable source
- **THEN** an unrelated enabled source does not bypass the ordinary HTTP 426 handshake denial

### Requirement: Source session shutdown releases all owned resources

Disconnect, cancellation, timeout, invalid input, ownership or settlement failure, and HA drain MUST close owned sockets and bound the session's wait for task and claim cleanup within existing cleanup budgets. Finalization that cannot complete within that wait MUST remain owned by tracked service cleanup until it settles or releases exactly once; stalled persistence MUST NOT delay transport closure or ASGI scope completion. Idle peer closure MUST NOT mark a healthy source unhealthy. Drain MUST stop new turns, let active work finish within the grace period, and close remaining sessions with restart semantics while completing settlement. Logs MUST identify downstream and upstream WebSocket transports separately from source identity, handshake status and generation outcome.

#### Scenario: A process drains during an active turn
- **WHEN** graceful shutdown begins while source output is streaming
- **THEN** further creates are refused, the active turn can finish within the grace budget, and completed finalization leaves no live task or quota/admission claim

#### Scenario: Persistence stalls during teardown
- **WHEN** disconnect or expired drain interrupts a turn whose settlement is still pending
- **THEN** both sockets close within transport cleanup budgets independently of persistence
- **AND** the scope waits only within the existing cleanup deadline and transfers any unfinished finalization to tracked service cleanup without abandoning or duplicating settlement

#### Scenario: Initial reservation acquisition stalls
- **WHEN** drain expires or the client disconnects while the first source turn is acquiring its quota reservation
- **THEN** the session closes within cleanup budgets without waiting indefinitely for acquisition
- **AND** tracked cleanup retains any admission claim and releases a reservation that commits after cancellation exactly once without contacting the provider

#### Scenario: Initial source lookup stalls
- **WHEN** the initial policy or source lookup stalls and the client disconnects or drain expires
- **THEN** the session MUST close within cleanup budgets without waiting for the full request timeout
- **AND** preparation MUST retain ownership of any late result without losing accepted input during a recoverable validation error or subscription handoff

#### Scenario: Input arrives during initial backend preparation
- **WHEN** more frames arrive before initial backend preparation finishes
- **THEN** read-ahead MUST be bounded by frame count and total bytes and preserve raw frame order and receipt timestamps across subscription handoff
- **AND** source selection MUST retain the one-waiting-turn limit, while read-ahead overflow MUST close the socket without dispatching buffered excess work

#### Scenario: An active deadline expires during persistence
- **WHEN** quota acquisition, ownership publication or settlement defers cancellation past the generation deadline
- **THEN** an independent deadline observer MUST close transports and bound ASGI scope completion
- **AND** unfinished persistence MUST remain owned by tracked cleanup until exactly one finalization completes, preserving the observed terminal outcome

#### Scenario: A generation times out while the client is slow
- **WHEN** a timed-out generation cannot promptly deliver its error downstream
- **THEN** its upstream is closed before its source admission slot is released

#### Scenario: Dashboard timeout policy overrides the environment
- **WHEN** the dashboard configures request, connect or downstream idle timeouts
- **THEN** source WebSocket sessions use the same effective timeout policy as subscription sessions

### Requirement: Catalog WebSocket preference reflects authorized source capability

A source public model SHALL advertise WebSocket preference only when every authorized eligible equivalent source is streaming and WebSocket-capable, the runtime supports source forwarding and global policy permits it. Mixed-capability pools MUST remain conservative. Catalogs MUST NOT expose source identifiers, credentials or private alias mappings. An HTTP-only recorded owner MUST NOT be replaced by another source to satisfy a WebSocket preference.

Discovery eligibility MUST mean enabled Responses sources and enabled models permitted by the key, evaluated before filtering streaming or WebSocket capability.

Catalog WebSocket capability MUST reflect the model and source pool selected by effective key/model routing policy. Evaluation MUST preserve explicit raw source-alias precedence, canonical fallback, exact source model permissions and source assignment scope. Fast-mode prohibition MUST evaluate the canonical effective model instead of the prohibited alias. Model enforcement MUST be applied before capability evaluation. The complete eligible pool for that effective model MUST be checked before streaming or WebSocket filtering.

#### Scenario: A capable alias resolves to an HTTP-only effective source
- **GIVEN** a key enforces `gpt-5.4-fast`, its fast-alias source supports native WebSocket, and its `gpt-5.4` source supports only HTTP
- **WHEN** fast mode is prohibited
- **THEN** the source catalog preference MUST disable WebSocket for that effective request

#### Scenario: An enforced alias falls back to a capable canonical source
- **GIVEN** a source-only key enforces `gpt-5.4-high`, no eligible source exposes that exact alias, and every eligible `gpt-5.4` source supports native WebSocket
- **WHEN** global transport policy permits WebSocket
- **THEN** its source capability evaluation MUST allow WebSocket using canonical source eligibility

#### Scenario: Equivalent sources have mixed capabilities
- **WHEN** one authorized equivalent source supports WebSocket and another supports only HTTP
- **THEN** the catalog does not prefer WebSocket and an anchored HTTP-only owner cannot be replaced during a WS request

### Requirement: Source keepalive resources remain bounded without pongs

Source WebSocket keepalive MUST consume bounded bookkeeping resources independent of the number of successful turns or unanswered pings on a reused connection. Periodic transport pings MUST continue without a pong deadline. Delayed pongs MUST NOT corrupt subsequent turns, and connection closure MUST terminate its keepalive work. Existing response deadlines, ownership, no-replay and settlement rules MUST remain in effect.

#### Scenario: Reused source never answers pings
- **WHEN** a source completes repeated turns without answering transport pings
- **THEN** successful responses and connection reuse MUST continue while keepalive bookkeeping stays bounded independently of turn count

#### Scenario: Provider resumes pong delivery
- **WHEN** a source sends delayed pong frames after completing a response
- **THEN** the connection MUST remain usable for the next owned continuation

#### Scenario: Source connection closes
- **WHEN** the source connection closes after success, timeout, peer closure or client cancellation
- **THEN** its keepalive work MUST terminate and MUST NOT retain pending pong state
