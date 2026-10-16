## Context

See [proposal.md](proposal.md) for the problem and scope, and [verification.md](verification.md) for implementation evidence and remaining conformance checks. The following decisions describe the implemented native sequential profile.

The HTTP source path already enforces more than basic forwarding:

| Existing area | Relevant responsibility |
| --- | --- |
| `app/modules/proxy/api.py`, `_balanced_source_responses_response` / `_source_responses_response` | Candidate-specific overrides, original/effective public-model ownership, portability, admission before reservation, failover |
| `source_dispatch.py` | One owner for attempt accounting, cleanup and logging; currently coupled to HTTP request/stream types |
| `source_ownership.py` | Durable references scoped by client key, public model and credential/endpoint/model revision; publication before delivery |
| `source_pool.py`, `source_admission.py` | Replica-local load, cooldown and concurrency limits |
| `app/modules/proxy/_service/websocket/mixin.py` | Subscription session, preparation, reservations and two source rejection guards: initial connect and socket reuse |
| `app/modules/proxy/api.py`, `_websocket_upstream_transport_denial` | Pre-upgrade gate for explicit HTTP mode and recent subscription WebSocket transport failures |
| `app/modules/model_sources/catalog.py` | Source model preference currently always false |

Current WebSocket preparation applies subscription transformations and reserves quota before the source guard. Source dispatch therefore needs a routing decision earlier than that guard. The existing source-probe helper fails open on database errors; that is unsuitable once it selects real source traffic.

## Goals / Non-Goals

**Goals:** Native Responses WebSocket with source isolation, persistent multi-turn connections, existing source accounting semantics and testable failure handling. Keep the shared HTTP path stable while extracting only the logic both transports need.

**Non-goals:** See the proposal. In addition, v1 does not transparently switch backend families or public models inside one downstream connection. Clients open a new connection for those changes. It does not promise full support for every extension in the evolving OpenAI WebSocket protocol.

## Decisions

### 1. Explicit capability and conservative activation

Add a non-null `ModelSource.supports_responses_websocket` boolean, server/application default false; API spelling follows existing snake_case/camelCase conversion. An enabled value requires `supports_responses=true`. A particular model also needs `supports_streaming=true`. Validate the resulting row on PATCH, including attempts to turn Responses off while WebSocket stays on.

Keep `base_url` HTTP(S). Derive the WS URL from the same resolved `/responses` endpoint, then change only the scheme to WS(S). This avoids a second endpoint setting and preserves alias/revision identity across transports. A provider requiring a different WebSocket endpoint or nonstandard credentials is outside this profile until explicitly supported.

The existing source edit form gains one checkbox under Responses. No new environment settings or navigation item. Source HTTP requests continue to use HTTP regardless of this flag.

### 2. Shared source resolution before subscription preparation

Extract a narrow source request/attempt layer from the HTTP route into the proxy source modules. Inputs are validated public request data, original model/effort, key policy and request metadata; outputs describe subscription routing, source routing, or an explicit denial. Avoid an extensible multi-protocol framework.

The shared layer retains raw/enforced aliases, current assignment scope, per-candidate overrides, both original and effective ownership checks, file checks and hosted-tool references. Subscription-owned anchors, uploaded files, structural compaction handling and required-capability routing retain precedence. Source lookup failure is an explicit error before admission/reservation; it cannot silently select a subscription account.

Resolve an explicit subscription turn-state owner under the exact API key before source selection, including creates without a previous response. Recheck it on reused sockets. Missing synthesized source placeholders remain valid; conflicting or unavailable owner lookup fails closed. Effective overrides introducing a subscription previous response also retain account ownership.

Move subscription-only normalization, anchor injection and subscription reservation after this decision. Both initial WebSocket connect and later `response.create` use it. Do not represent a source as an `Account`.

A subscription outcome from the source callback is a handoff for validation;
it does not itself bind the socket. Set the subscription binding only after
subscription preparation and ownership validation succeed, so a recoverable
first-frame validation error still permits a corrected source create.

Render domain errors through the existing WebSocket error formatter before
adapting them into source preparation errors. The callback precedes backend
selection and also wraps source quota acquisition, so it must retain the
authentication, permission and rate-limit types and parameter fields observed
by subscription and HTTP clients.

The shared ownership-miss helper constructs errors with the existing HTTP
`openai_error` factory before either transport renders them. An unavailable
recorded owner is a 409 `server_error`; selecting the transport must not
reclassify it as invalid client input, even when native WebSocket support is off.

### 3. Separate session ownership from turn ownership

Use a focused `SourceWebSocketSession` with one reader, one serialized sender, one reusable upstream connection and bounded pending work. A turn owns admission, one quota reservation, an ownership recorder and one attempt log. The session owns the socket and tasks. Finishing a turn releases its claims but preserves a healthy idle socket; finishing the session cancels/awaits tasks and closes that socket exactly once.

Start initial preparation inside the session lifecycle. A read-ahead task observes disconnect and preserves raw frames plus receipt timestamps in a 16-frame/16-MiB buffer; stop and await that reader before subscription handoff or source receive ownership. Overflow closes the socket with a best-effort queue error. Source queue admission remains one waiting create after backend selection.

A lifecycle task independently observes the active absolute deadline, including a selected source's shorter preparation budget. It can close transports while critical persistence defers cancellation. Bound the scope's cleanup wait, retain unfinished tasks in the existing service cleanup set, and consume late acquisition results for one release. Preserve a terminal already handed to the transport; do not send a contradictory timeout after it. Reservation heartbeat errors are logged and retried on the next interval, with a separate database session per touch.

Keep observed usage separate from client delivery: cancellation before any native content handoff releases even when a withheld terminal contained usage. Apply this rule to native dispatch only so the existing HTTP accounting contract remains unchanged. A successful terminal already handed off retains success, and previously delivered partial content retains cancellation accounting.

Generalize `SourceDispatch` only where needed: request metadata instead of an HTTP `Request`, transport labels, and a per-turn release handle distinct from connection shutdown. Add event-level usage/ownership observation used by SSE and JSON WebSocket envelopes; do not serialize JSON through a synthetic SSE stream merely to reuse parsers.

V1 permits one active generation and one queued `response.create`. Additional creates receive a bounded busy error and no reservation. The next turn waits for the preceding accounting finalizer, even if its terminal frame was already delivered. Use existing clock/scheduler seams and separate database sessions for concurrent operations.

Queued work holds no inference admission/reservation. Its deadline includes time since receipt and it is revalidated at dequeue; waiting never resets the request budget. Busy/unsupported-control errors are associated with the rejected input, never the active response ID or its accounting terminal. Compatibility fixtures must verify clients do not confuse these with the active response's completion.

The connection binds to the selected source, credential revision, public model and effective upstream model. Recheck permissions and source capability each turn. A required identity/backend change receives a reconnect-required terminal error; never send it on the existing upstream. Source disable/revocation/key rotation stops admission of later turns; an already admitted turn retains its cleanup owner.

### 4. Native source adapter and supported protocol

Build a dedicated adapter under `app/modules/model_sources/`, using the existing WebSocket library and source credential decryption. Upstream authentication comes from the selected source. Do not forward the downstream Bearer key, cookies, ChatGPT account identifiers, installation identifiers or subscription fingerprint headers. Do not follow handshake redirects.

Support JSON text `response.create`, normal Responses events, sequential tool-result continuation and `generate:false` warmup for providers verified against this profile. Remove HTTP-only `stream`/`background` envelope fields; reject active background mode and unsupported nondefault transport options before sending. Keep source alias, instructions, reasoning effort and override handling identical to HTTP.

Classify warmup from the final effective payload after overrides. Reserve its input budget with zero generated-output estimate; settle reported usage when present, otherwise use the input estimate with zero output on successful warmup. Never write estimated tokens as observed upstream usage. Unexpected generated output from a no-generation warmup is withheld and ends the attempt as an invalid upstream response under the existing error-release policy. This prevents charging a normal generation estimate for a no-output warmup while retaining input accounting.

V1 does not accept named `stream_id`, `response.steer`, or application `response.cancel`; reject unsupported controls before dispatch without altering an active turn. Cancellation uses downstream connection close. Ping/pong control frames remain transport-level. Binary, malformed and oversized frames have explicit validation/close behavior. Client compatibility fixtures must prove this subset before advertising support; a client that requires excluded controls is not yet supported.

Retain the original UTF-8 message size alongside receipt time at the shared input boundary. Once initial routing establishes a source backend, enforce the same 16-MiB limit used for reused source input before claiming admission or quota. Count original wire bytes, including whitespace and JSON escapes, rather than reserializing the parsed object. A rejected first source create leaves the socket unbound and permits a corrected create. Subscription routing keeps its existing larger ingress limit and strict duplicate capability parsing.

Reuse current source connect/first-event/idle/total budgets where applicable. The request deadline uses the shared Responses stream-budget helper: `http_responses_stream_request_budget_seconds` takes precedence, and the dashboard-overridden generic proxy budget applies only when the stream-specific setting is absent. A selected source's shorter timeout still limits the turn. Interpret `timeout_seconds` per generation, not as the lifetime of the reusable socket. Keep socket count, inbound queue and outbound buffered bytes bounded; an idle socket holds no inference admission or quota reservation. Slow consumers trigger bounded cleanup, not unbounded event accumulation.

### 5. Ownership, replay and reconnection

Keep durable source ownership scoped by the exact client key and public model. Publish all introduced response/item/call/encrypted/hosted-resource references before the corresponding downstream event. Publication failure stops delivery and finishes the attempt without success or source failover. Adding a capability does not change the existing HTTP credential revision hash.

Initial portable requests can use the existing source pool filtered to WebSocket-capable candidates, with at most five distinct attempts. Retry only proven pre-submission connection failures or explicit handshake rejection classes already allowed by source policy. Once sending `response.create` begins, treat delivery as ambiguous on failure and do not automatically replay. Finish the old reservation/admission before cooldown or another attempt. An owned continuation never moves to another source or credential.

Before any send, a source can lose admission during an effective-policy await. Exclude that candidate and reselect only for a portable unbound request, bounded to five candidates. Recheck replacement capability, controls, ownership and remaining budget before claiming admission; the earliest selected source/request deadline cannot grow.

Return that session deadline to each prepared turn as well as the lifecycle observer. Connection retries use it too, so preparing a replacement with a longer timeout cannot overwrite the observer's earlier deadline when the worker installs the new turn.

On reconnect, durable ownership identifies the source, not the contents of the old upstream socket. An anchored request can reconnect only to its owner; a provider's missing connection-local state produces the existing safe continuity error. Never silently erase `previous_response_id`, synthesize missing history or replay generated tool effects. The client can explicitly restart with full, self-contained input.

The same rule governs HTTP→WS, WS→HTTP and another HA replica: owner identity is preserved; success depends on provider state being recoverable over that transport. There is no cross-replica live-socket migration.

### 6. Transport policy and compatibility

| Condition | Proposed behavior |
| --- | --- |
| Dashboard explicitly selects `http` | Keep ordinary downstream Responses handshake denial with HTTP 426; source opt-in does not override it |
| Dashboard `auto` or `websocket`, source capability on | Native source WebSocket eligible |
| Source capability off | Keep HTTP source behavior and `model_source_requires_http_transport` on a WS turn; never select a subscription account for it |
| Subscription transport recently failed | Preserve its recovery behavior for account-only traffic; do not reject an authorized WS source solely because of that marker |
| Source transport failed | Source-scoped cooldown/error; no writes to subscription transport-failure or account-health state |

For mixed/unknown model scopes, decide the actual backend from the first validated create frame. A known eligible source scope may pass the recent-subscription-failure gate, but that does not waive required-capability/auth checks. A subsequently selected subscription backend keeps its normal error/recovery behavior. In-band errors after a 101 upgrade cannot become an HTTP 426 response.

Existing comments disagree about whether client fallback follows an in-band 503 or requires a handshake 426. The plan guarantees error envelopes and no unsafe forwarding, not transparent fallback on every client. The first milestone records actual installed-client behavior. Clients can always explicitly select HTTP for an HTTP-only source.

### 7. Discovery and observability

Only advertise source `prefer_websockets=true` when the authorized equivalent candidate set for that public model is entirely streaming and WS-capable, the global policy permits WS, and the runtime implementation is present. Mixed-capability pools remain conservative; explicit WS requests still resolve capable candidates without overriding an HTTP-only recorded owner.

For discovery, determine candidates from enabled Responses sources and enabled models inside the key's scope before filtering streaming/WS capability. Otherwise filtering out HTTP-only candidates would incorrectly advertise a mixed pool as fully capable.

Resolve each advertised/requested model through the same alias candidate ordering as source routing: enforce the key's model, normalize aliases, preserve an explicitly configured raw source alias unless fast-mode prohibition removes it, then apply exact model permission and subscription-registry precedence. Evaluate capability across the complete pool for the selected effective model. Catalog preferences use this decision. For example, prohibiting `gpt-5.4-fast` selects `gpt-5.4`; a capable fast-alias source cannot advertise support when that canonical source is HTTP-only.


The provider flag remains frozen at export. Re-running an old script refreshes its catalog but not that embedded flag; a newly exported script evaluates current assignment/capability/global policy. Runtime routing remains authoritative if capabilities change after export.

Model names with no eligible Responses candidate are absent from the capability
aggregate, rather than represented as incapable sources. For example, adding a
Chat-only or disabled model to an allowlist must not disable the native-capable
Responses models. Keep HTTP-only and nonstreaming Responses peers in the
aggregate, and keep an empty set (including an enforced model without a
candidate) HTTP-only.

Log each source attempt with public model, actual source, `transport=websocket`, an explicit source-WS upstream transport label, handshake status when observed, usage/timing and the safe terminal cause. Keep handshake status separate from generation success. Reuse source failure/cost accounting; idle closure is not source unhealthiness. Add only bounded-cardinality metrics needed to distinguish handshake, active turn, disconnect and cleanup failures.

## Risks / Trade-offs

- Provider says “OpenAI-compatible” but supports HTTP only → explicit opt-in plus a deterministic local WS provider fixture and a staging conformance run against the intended provider before enablement.
- Shared extraction disturbs mature HTTP guards → land a behavior-preserving extraction with current source pool, ownership, payload and settlement regressions before routing changes.
- Sequential-only protocol excludes some clients → test actual client traffic and document the supported subset; extend the spec before claiming multiplex/steering support.
- Persistent sockets reduce per-turn load rotation → balance at session creation, admit/count each generation, bound idle sockets and keep hard ownership authoritative.
- Source state disappears at reconnect → fail with a continuity error instead of implying that same-key reconnection reconstructs state.
- Mixed-client fallback differs after accepting a socket → test first-turn and reuse failures explicitly; no unverified promise of automatic HTTP fallback.

## Migration Plan

Implement in the ordered work packages in [tasks.md](tasks.md), with focused PRs within repository review limits. The additive capability migration lands off by default. UI/catalog activation waits for the entire route/lifecycle path and tests; intermediate changes must not advertise a usable capability prematurely.

Deploy compatible code to every backend before an operator enables a source. Stop admitting new turns during drain, complete active turns within the configured grace budget, then close sockets with restart semantics; forced expiry still owns settlement. Feature disablement prevents new source turns. A future rollback disables the capability and regenerates affected client config before returning to an older runtime; do not downgrade a live schema while newer replicas remain.

The runtime and stable specifications are implemented and synced locally. Deployment, production capability enablement, commit and PR publication were not requested. Archive waits for the remaining verification gates in [tasks.md](tasks.md).
