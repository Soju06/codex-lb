## ADDED Requirements

### Requirement: Client-facing Responses error params are canonicalized

The presence-aware raw `OpenAIErrorParam` state MUST remain available to
internal classification, request matching, and replay authorization. At every
client-facing Responses serialization boundary, however, an error `param` MUST
be emitted only when its normalized value is a non-empty string; the emitted
value MUST be trimmed. Explicit null, number, boolean, object, array, blank,
and whitespace values MUST be omitted. Public masking MUST remain independent
of replay authorization: a malformed present value MUST fail closed for
recovery while a canonical stale-anchor error is still masked without exposing
its raw value. When a masked `response.failed` already carries the current
downstream response id, that id MUST be preserved while the stale upstream id
and raw error details are removed.

#### Scenario: malformed params cannot cross a Responses stream boundary

- **GIVEN** an upstream `error` or `response.failed` carries a malformed present `param`
- **WHEN** the event is serialized for `/v1/responses` or a Codex Responses client
- **THEN** the client event omits `param`
- **AND** the raw value is absent from the serialized event
- **AND** the malformed value does not authorize replay or full-history recovery

#### Scenario: valid params are trimmed at the public boundary

- **GIVEN** an upstream error carries `param = "  input  "`
- **WHEN** the event is serialized for a client
- **THEN** the client event carries `param = "input"`

#### Scenario: masked failure retains the current downstream id

- **GIVEN** an upstream stale-anchor `response.failed` carries the current downstream response id
- **WHEN** public masking rewrites the failure
- **THEN** the rewritten failure retains that current id
- **AND** it omits the stale upstream id and malformed parameter

## MODIFIED Requirements

### Requirement: Codex WebSocket stale-anchor failures remain recoverable by a full-context retry
When serving or consuming the Codex-native `/backend-api/codex/responses` WebSocket route, upstream `previous_response_id` MUST be treated as an ephemeral optimization rather than durable conversation state. A confirmed stale-anchor continuity failure during a long-wait tool-output continuation MUST NOT hard-end the user turn before one full-context retry without `previous_response_id` has been attempted — either by the proxy replaying a retained body, or by the compatible client the proxy signals. Exactly one of those two paths MUST run, chosen by the retry boundary below.

The proxy MAY run that full-context retry itself only when it retained, for the same turn, a self-contained retry-safe body: a body that carries the whole conversation input needed to reproduce the turn on its own, and that pairs every `function_call_output`, `custom_tool_call_output`, and `apply_patch_call_output` item with its matching tool-call item in the same payload. The proxy MUST replay such a body at most once and MUST remove `previous_response_id` from the replay.

The proxy MUST NOT replay an output-only body as a fresh turn. A body whose `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items have no matching tool-call item in the same payload carries no conversation state of its own; replaying it without `previous_response_id` would fabricate a turn out of tool results whose calls upstream never saw. For any body that is not retained and retry-safe, the proxy MUST fail the turn closed instead of retrying, and MUST require the compatible client to resend full context by surfacing the sanitized canonical signal below.

The sanitized signal the service surfaces for a Codex-native stale-anchor failure MUST be the canonical `previous_response_not_found` error code, because that is the code an unmodified Codex client acts on to recover; the service MUST NOT substitute a proxy-specific classifier that standard clients do not recognize, and MUST NOT expose the raw upstream error envelope or the missing upstream response id. That canonical code is reserved for a confirmed upstream rejection of the request's `previous_response_id`; an ownership failure MUST use the separate retryable owner-unavailable errors required by "Hard continuity owner lookup fails closed".

#### Scenario: Long-running terminal wait invalidates the upstream previous response anchor
- **GIVEN** a Codex-native WebSocket session has completed a response with id `resp_old`
- **AND** the client later sends a `response.create` frame with `previous_response_id: "resp_old"` and tool-output or other delta input after a long idle period
- **WHEN** the upstream rejects `resp_old` with a stale-anchor error such as `previous_response_not_found`
- **THEN** the failure is classified as stale-anchor continuity loss
- **AND** because that delta input is not a retained self-contained retry-safe body, the proxy does not replay it and the downstream signal uses `error.code = "previous_response_not_found"`, which an unmodified Codex client's built-in stale-anchor recovery retries once using full conversation history without `previous_response_id` before surfacing a turn-ending error
- **AND** the downstream payload does not expose the raw upstream error envelope or the missing upstream response id

#### Scenario: Retained self-contained body is retried by the proxy without the rejected anchor
- **GIVEN** a Codex-native WebSocket `response.create` whose retained replay body carries the whole conversation input and pairs every tool-output item with its matching tool-call item in the same payload
- **WHEN** upstream rejects `previous_response_id` with a stale-anchor error before `response.created`
- **THEN** the proxy replays that retained body once with `previous_response_id` removed
- **AND** it dispatches no second replay for the same turn
- **AND** a successful replay surfaces no stale-anchor error downstream

#### Scenario: Output-only tool results are never replayed as a fresh turn
- **GIVEN** a Codex-native WebSocket `response.create` whose input carries `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items whose matching tool-call items are absent from the same payload
- **WHEN** upstream rejects `previous_response_id` with a stale-anchor error before `response.created`
- **THEN** the proxy MUST NOT replay that body as a fresh turn without `previous_response_id`
- **AND** the turn fails closed carrying the sanitized canonical `previous_response_not_found` code, which requires the compatible client to resend full context itself
- **AND** the downstream payload does not expose the raw upstream error envelope or the missing upstream response id

#### Scenario: codex-lb sanitizes stale-anchor errors for client classification
- **WHEN** upstream emits a direct Codex-native WebSocket stale-anchor error
- **THEN** codex-lb MUST surface it with the canonical `error.code = "previous_response_not_found"` so an unmodified Codex client recognizes stale-anchor continuity loss without proxy-specific knowledge
- **AND** codex-lb MUST NOT forward the raw upstream error envelope or expose the missing upstream response id downstream
- **AND** codex-lb MUST NOT substitute a proxy-specific classifier that standard Codex clients do not act on
- **AND** the signal MUST let a compatible Codex client distinguish stale-anchor continuity loss from quota, policy, auth, and generic invalid-request failures

#### Scenario: Public /v1 responses keep generic continuity masking
- **WHEN** the stale-anchor failure is served to an OpenAI-compatible `/v1/responses` WebSocket client rather than the Codex-native route
- **THEN** the downstream event remains a retryable `stream_incomplete` continuity failure
- **AND** the downstream payload does not expose `previous_response_not_found` or the missing upstream response id
- **AND** the same retry boundary applies unchanged: a retained self-contained retry-safe body is still replayed once without `previous_response_id`, and an output-only body is still never replayed as a fresh turn

#### Scenario: Public masking is independent of the fail-closed recovery classifier
- **GIVEN** upstream returns a canonical `previous_response_not_found` error whose `param` is present but blank, whitespace-only, null, or a non-string JSON value
- **WHEN** that error is served to a public `/v1` client on the streaming, WebSocket, or non-streaming response path
- **THEN** codex-lb MUST mask it as a generic `stream_incomplete` server error
- **AND** the downstream payload MUST NOT contain the `previous_response_not_found` code, the raw malformed `param`, or the missing upstream response id
- **AND** the stale-anchor recovery classifier MUST still fail closed on that same error, so no anchor removal, replay, reconnect, or client full-history resend is authorized by it
- **AND** the opt-in `client_full_history_once` recovery mode MUST NOT pass the upstream-shaped 400 through for a malformed-`param` error
- **AND** the fail-closed outcome MUST be recorded with a continuity reason distinct from a proven stale-anchor miss

#### Scenario: Non-stale-anchor failures do not trigger full-context retry
- **WHEN** the upstream failure is quota, policy, auth, context-window, or another non-continuity error
- **THEN** the client MUST NOT convert it into a stale-anchor full-context retry
- **AND** codex-lb MUST preserve the original error class as much as safely possible

#### Scenario: ChatGPT backend omits param on an invalid previous response id
- **GIVEN** a request depends on a `previous_response_id`
- **WHEN** upstream returns `code = "invalid_request_error"` with a message whose normalized form is exactly `Invalid previous_response_id.`
- **AND** `param` is absent or equals `previous_response_id`
- **THEN** codex-lb MUST classify the failure as stale-anchor continuity loss
- **AND** the existing one-shot replay or sanitized canonical client signal MUST run
- **AND** the same generic error with another `param` MUST NOT trigger continuity recovery
- **AND** unrelated `invalid_request_error` messages MUST NOT trigger continuity recovery

#### Scenario: Verified HTTP full resend escapes a rejecting owner
- **GIVEN** an HTTP-bridge continuation carries a full input history that passes the existing durable full-resend and account-neutral projection checks
- **AND** the projected request has no account-scoped file references
- **WHEN** the continuity owner explicitly rejects its `previous_response_id` as not found before producing a response
- **THEN** codex-lb MUST remove the rejected anchor and replay the verified full request at most once on a fresh account-neutral bridge
- **AND** the rejecting owner account MUST be excluded from that replay
- **AND** stale session and turn-state affinity headers MUST NOT be forwarded to the replacement bridge
- **AND** the durable operation identity and settlement contract MUST remain attached to the replacement attempt
- **AND** anchor removal MUST NOT occur unless a registered durable operation id and the current durable session owner fence are available
- **AND** failure to reset the fenced operation spool MUST abort before the unanchored replacement is submitted
- **AND** delta-only or unverified requests MUST fail closed
- **AND** verified file/account-bound requests MUST NOT migrate accounts and MAY use only the same-owner replay described below
- **AND** an eventless transport failure without an explicit stale-anchor rejection MUST NOT by itself authorize cross-account replay
- **AND** no anchored or unanchored replacement MUST run when the request has already consumed an eventless replay, including delta-only and prefix-unverified requests
- **AND** an UNKNOWN recovery journal on an inactive durable owner MUST fail closed without being claimed for anchor removal or account migration
- **AND** explicit stale-anchor replacement MUST NOT depend on claiming the ambiguous-transport recovery journal
- **AND** durable full-resend safety MAY be proven either by retained prior output or by an exact stored pending-tool-call manifest match
- **AND** account-neutral replay admission MUST atomically claim the authorized original hard-key circuit generation
- **AND** that generation claim MUST use the original hard session key even though the replacement uses a new account-neutral key
- **AND** that generation claim MUST apply only to the local/durable circuit generation observed when recovery was authorized
- **AND** that generation claim MUST run for absent, below-threshold, expired, half-open, and active circuit states
- **AND** that generation claim MUST use a monotonic field independent from failure observation time so delayed clock-skewed failures remain mergeable
- **AND** a circuit generation that wins the durable compare-and-set first MUST suppress the account-neutral replacement before submit
- **AND** a timed-out durable claim MUST reconcile against durable state before suppressing the replacement, and MUST suppress it unless that reconciliation proves the authorized generation was still unconsumed
- **AND** the durable claim and its reconciliation MUST be bounded by the remaining request deadline, and MUST suppress the replacement without further durable I/O once that deadline is spent

#### Scenario: Verified owner-bound HTTP full resend drops only the rejected anchor
- **GIVEN** an HTTP-bridge continuation carries a prefix-verified, trim-safe full input history
- **AND** the retained request is not account-neutral because it contains account-bound tool or file history
- **WHEN** the continuity owner explicitly rejects its `previous_response_id` as not found before producing a response
- **THEN** codex-lb MUST remove the rejected anchor and replay the retained full request at most once on the same owner
- **AND** the replacement upstream request MUST NOT contain the rejected `previous_response_id`
- **AND** same-owner replay MUST use a unique owner-pinned internal key instead of bypassing the older hard-key retry circuit
- **AND** same-owner replay admission MUST NOT consume the original hard-key circuit generation, because its unique owner-pinned internal key already keeps it off that admission path
- **AND** same-owner replay MUST retain the original hard session key only for independent quarantine reconciliation
- **AND** local and durable retry-circuit state MUST NOT be deleted to authorize that replay
- **AND** successful completion of that verified replay MUST NOT clear the pre-existing local or durable circuit state
- **AND** successful completion MUST clear independent bridge quarantine state for both the replacement key and original hard key
- **AND** the request MUST NOT migrate to another account
- **AND** durable operation identity and settlement MUST remain attached to the replacement attempt
- **AND** a missing operation ledger, operation id, durable session id, owner epoch, or missing, refused, or failed spool reset MUST retain the anchor and fail closed
- **AND** delta-only or prefix-unverified requests MUST retain the existing fail-closed behavior
- **AND** an eventless transport failure without an explicit stale-anchor rejection MUST NOT by itself authorize this replay
- **AND** ordinary transport recovery without an explicit stale-anchor rejection MUST NOT bypass or clear the retry circuit
- **AND** an operation-journal recovery after an ambiguous transport failure MUST NOT remove the anchor or migrate accounts
- **AND** a request with a nonzero replay count MUST NOT dispatch another stale-anchor replacement
- **AND** a present blank or whitespace-only `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** a present non-string or null `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** blank or whitespace-only parameter presence MUST survive every internal upstream event-normalization layer and MUST NOT be rewritten to the canonical continuity code
- **AND** the verified replacement MUST NOT receive a clean-close or other transport-level resend after its first dispatch
- **AND** a local or durable circuit failure recorded after recovery authorization MUST NOT suppress the same-owner replacement
- **AND** verified replacement MUST NOT enter authentication replay
- **AND** HTTP-bridge terminal normalization MUST preserve a present empty parameter in the internal normalized error state/envelope for classification and matching
- **AND** any client-facing Responses serializer MUST omit that malformed parameter, as required by the client-facing canonicalization requirement above
- **AND** inactive-owner UNKNOWN journal inspection MUST apply to both account-neutral and owner-bound verified full resends
- **AND** a pre-dispatch failure after rebinding an existing durable operation MUST restore that operation's failed fence and MUST NOT delete its row
- **AND** a durable operation snapshot MUST distinguish newly inserted rows from rebound rows
- **AND** generic eventless transport retry MUST NOT convert an anchored safe-fresh request into an unanchored replay without explicit stale-anchor rejection
- **AND** rebound rollback MUST restore the prior session/account/model/parent ownership fields
- **AND** a restored rebound operation MUST retain its durable identity and require a fresh rebind before any in-memory capacity or gate retry dispatches
- **AND** successful replacement completion MUST clear the original-key quarantine only when it still matches the generation observed at recovery authorization
- **AND** quarantine generations MUST remain unique for the service lifetime across TTL and size-cap pruning so a stale completion cannot clear a reused key
- **AND** explicit stale-anchor rejection after any emitted response event or downstream-visible output MUST fail closed without anchored fallback dispatch

#### Scenario: Anchored same-owner rebind treats spool reset as best effort
- **GIVEN** an anchored HTTP-bridge continuity retry is locally rebound without removing its `previous_response_id`
- **WHEN** the recovery spool reset capability is missing, returns a falsy result, or raises an exception
- **THEN** the existing same-owner anchored rebind MUST continue without aborting on the spool-reset outcome
- **AND** the original `previous_response_id` MUST remain anchored on the rebound request
- **AND** the reset outcome MUST NOT produce a `bridge_continuity_persistence_failed` error or dispatch an unanchored replacement

### Requirement: Direct WebSocket previous-response misses never leak raw upstream errors
When a direct Responses WebSocket request depends on `previous_response_id`, the service MUST NOT send the raw upstream `previous_response_not_found` error envelope or the missing upstream response id to the downstream client. On the Codex-native `/backend-api/codex/responses` route the service MUST surface the sanitized canonical `error.code = "previous_response_not_found"` (raw envelope and id removed) so an unmodified Codex client recovers; on public `/v1/responses` the service MUST rewrite the failure to a retryable `stream_incomplete` continuity error. This applies to both `/v1/responses` and `/backend-api/codex/responses` WebSocket clients.

#### Scenario: Codex Desktop continue receives upstream previous-response miss before response.created
- **WHEN** a Codex-native `/backend-api/codex/responses` WebSocket `response.create` request includes `previous_response_id`
- **AND** upstream emits a top-level `type=error` payload with `code=previous_response_not_found` or `param=previous_response_id`
- **AND** no stable upstream `response.id` has been assigned yet
- **THEN** the downstream client receives either a transparent replay result or a retryable `previous_response_not_found` error that carries no raw upstream envelope
- **AND** the downstream payload does not include the raw upstream error envelope
- **AND** the downstream payload does not include the missing previous response id

#### Scenario: Codex Desktop continue has only request-log owner metadata
- **WHEN** a prior direct WebSocket turn completed and was persisted only in `request_logs`
- **AND** a later direct WebSocket follow-up references that completed response id
- **THEN** owner lookup uses request-log metadata or fails closed with a retryable error
- **AND** it does not continue on an unpinned account
- **AND** a fail-closed ownership outcome uses the retryable owner-unavailable error required by "Hard continuity owner lookup fails closed", not `previous_response_not_found`, because upstream never rejected the anchor
- **AND** it does not expose the raw upstream error envelope or the missing previous response id

### Requirement: Hard continuity owner lookup fails closed

When a request depends on hard continuity ownership, the service MUST fail
closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT
continue with account selection that bypasses hard owner enforcement. A direct
WebSocket continuation already attached to its required open owner socket MUST
NOT be failed solely because a new per-turn selection attempt temporarily
excludes that owner.

The `previous_response_not_found` code is reserved for a confirmed upstream
rejection of the request's own `previous_response_id`. An ownership failure is
not that rejection: when ring, durable-session, or request-log ownership cannot
be proved, the anchor itself was never presented upstream or never refused. The
service MUST therefore classify an ownership failure as owner-unavailable rather
than stale-anchor, and MUST NOT surface `previous_response_not_found` for it on
any route — including the Codex-native `/backend-api/codex/responses` route,
where that code would make an unmodified client burn its one stale-anchor
full-context retry against a failure a resend cannot fix. The service MUST use a
separate retryable error instead: `upstream_unavailable` when the ownership
lookup itself failed, `previous_response_owner_unavailable` when a required owner
was resolved but cannot serve the request, and `bridge_owner_unreachable` on the
HTTP-bridge ownership path. An ownership failure MUST be recorded with a
continuity reason distinct from a proven stale-anchor miss.

#### Scenario: ownership failure is not reported as a stale-anchor rejection

- **GIVEN** a follow-up carrying `previous_response_id` on either the Codex-native
  `/backend-api/codex/responses` route or public `/v1/responses`
- **WHEN** ring, durable, or request-log ownership cannot be proved and upstream
  never rejected that `previous_response_id`
- **THEN** the terminal downstream error code is the retryable owner-unavailable
  error for that path (`upstream_unavailable`,
  `previous_response_owner_unavailable`, or `bridge_owner_unreachable`)
- **AND** the downstream error code is not `previous_response_not_found`
- **AND** the failure is recorded with a continuity reason distinct from a proven
  stale-anchor miss

#### Scenario: websocket previous-response owner lookup errors

- **WHEN** a websocket or HTTP fallback follow-up includes
  `previous_response_id`
- **AND** owner lookup errors prevent determining the required owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys

- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state,
  explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current
  replica

#### Scenario: required owner differs from the open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to an owner different from the
  currently open upstream account
- **THEN** the service retires the current upstream socket
- **AND** reconnects the unchanged anchored request to the required owner
- **AND** it does not forward any `x-codex-turn-state` associated with the
  retired account, whether supplied by the client or learned upstream

#### Scenario: required owner matches the healthy open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to the currently open owner
- **THEN** the service sends it on that socket without a new selector-based
  eligibility check

### Requirement: Dead durable anchors recover transparently when safe

When a continuity-bound HTTP bridge request is supplied by a durable
previous-response anchor whose owner instance, process epoch, or lease is
proven dead, the proxy MUST use the existing safe replay proof to dispatch a
fresh turn without that anchor. When a client-provided anchor cannot be safely
replayed as a fresh turn, the proxy MUST fail closed with the applicable
retryable owner-unavailable error and MUST record a continuity reason distinct
from a proven stale-anchor miss. It MUST NOT surface
`previous_response_not_found` for this path because upstream never rejected the
anchor.

#### Scenario: Previous-process anchor with replayable context recovers automatically

- **GIVEN** a request is bound to a durable previous-response anchor
- **AND** that durable row belongs to the same instance id but a different
  process owner epoch
- **AND** the payload has a safe full-context replay proof
- **WHEN** the bridge hits the pre-submit, startup-cooldown, or retry-circuit
  idle terminal path
- **THEN** the proxy dispatches the request as a fresh turn without the dead
  previous-response anchor
- **AND** the client receives the normal streaming response
- **AND** the response does not include `stream_idle_timeout` retry guidance or
  a bridge-specific recovery error

#### Scenario: Unreplayable client anchor uses the owner-unavailable contract

- **GIVEN** a request is bound to a client-provided durable previous-response
  anchor
- **AND** that durable row belongs to a dead owner
- **AND** the payload does not have a safe fresh-turn replay proof
- **WHEN** the bridge must fail closed
- **THEN** the client receives the retryable
  `previous_response_owner_unavailable` error because the durable previous-
  response owner was resolved but is unavailable
- **AND** the error code is not `previous_response_not_found`
- **AND** continuity failure metadata records reason
  `owner_account_unavailable`, distinct from a proven stale-anchor miss
- **AND** the response does not include a bridge-specific recovery code

#### Scenario: Current-owner silence remains retryable

- **GIVEN** a request is bound to a durable owner whose instance id, process
  owner epoch, and lease are current
- **WHEN** upstream produces no response events through the existing idle window
- **THEN** the proxy preserves the existing retryable `stream_idle_timeout`
  behavior

### Requirement: Codex WebSocket top-level previous-response errors are masked
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy MUST treat upstream `type: "error"` frames with top-level error fields as upstream error envelopes if the frame does not contain a nested `error` object. If those fields describe a `previous_response_not_found` continuity miss, the proxy MUST use the existing continuity fail-closed behavior and MUST NOT forward the raw upstream error envelope or the missing response id to the downstream Codex client. The proxy MUST surface the sanitized canonical `previous_response_not_found` code to the Codex-native client so an unmodified client recovers, while public `/v1/responses` clients receive `stream_incomplete`.

#### Scenario: ChatGPT backend emits top-level previous-response miss on Codex websocket
- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** the ChatGPT backend emits `{"type":"error","code":"previous_response_not_found","param":"previous_response_id",...}` without a nested `error` object
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Codex WebSocket wrapped errors follow official client shape

When serving `/backend-api/codex/responses` or bridge-backed Responses WebSocket traffic, the service MUST classify upstream `type: "error"` frames using the same wrapped-error shape that the official Codex client accepts: a non-2xx `status` or `status_code` field indicates an upstream HTTP-style error, and the error detail MAY appear either in a nested `error` object or in top-level fields such as `code`, `message`, `param`, and `error_type`.

Top-level error normalization MUST NOT treat the event discriminator `type: "error"` as the upstream error type. If the frame provides `error_type`, the service MUST use that value as the error type for classification/rewrites. Existing continuity protection remains authoritative: frames describing `previous_response_not_found` MUST be rewritten or recovered through the established continuity path, surfacing the sanitized canonical `previous_response_not_found` code on the Codex-native route and `stream_incomplete` on public `/v1/responses`, without exposing the raw upstream error envelope or the missing response id.

#### Scenario: status_code alias is classified as upstream error status

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status_code":400,...}`
- **THEN** the service treats the HTTP-style error status as `400`
- **AND** applies the same error classification path as for `status: 400`

#### Scenario: top-level error_type is used for classification

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status":400,"error_type":"invalid_request_error","code":"previous_response_not_found",...}`
- **THEN** the normalized error detail has `type = "invalid_request_error"`
- **AND** the event discriminator `type = "error"` is not used as the upstream error type

#### Scenario: top-level previous-response miss surfaces the sanitized canonical code

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id

#### Scenario: top-level previous-response miss remains masked

- **WHEN** a public `/v1/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable `stream_incomplete` continuity failure
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id
- **AND** the downstream payload does not expose the `previous_response_not_found` code

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
response event MUST remain excluded from this pre-response circuit.

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

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
and durable circuit state only after a successful durable read establishes the
version fence. When that read fails, the proxy MUST retain local admission
state and MUST skip any unfenced durable clear so a newer concurrent failure
cannot be erased.

#### Scenario: idle bridge retirement does not consume a circuit strike

- **GIVEN** a hard-affinity HTTP bridge has no pending requests
- **WHEN** its upstream WebSocket closes and the idle bridge is retired
- **THEN** the retry-circuit failure count for that key remains unchanged
- **AND** a later request is not placed in cooldown because of the idle close

#### Scenario: eventless pending retirement consumes exactly one strike

- **GIVEN** a hard-affinity HTTP bridge owns a pending request with no observed response event
- **WHEN** the bridge retires because the upstream fails before acknowledging the request
- **THEN** the retry circuit records exactly one failure for that request lifecycle

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

#### Scenario: durable clear lookup failure preserves a newer failure

- **GIVEN** a terminal success begins clearing a hard-key retry circuit
- **AND** the durable lookup fails while a newer failure is committed
- **WHEN** the terminal cleanup settles
- **THEN** the proxy does not issue an unfenced durable clear
- **AND** the newer durable failure remains authoritative
- **AND** the local admission guard remains available on the clearing replica

#### Scenario: durable retry-circuit clear is version and generation fenced

- **GIVEN** a terminal success begins clearing a hard-key retry circuit
- **AND** a successful durable lookup returns the persisted update version and admission generation
- **WHEN** the terminal cleanup issues its durable clear
- **THEN** the clear MUST atomically match both observed fences
- **AND** a zero-row conditional clear MUST be treated as a newer durable state winning
- **AND** the newer durable failure MUST remain authoritative
- **AND** the local admission guard MUST remain available on the clearing replica

#### Scenario: durable retry-circuit clear lookup failure preserves local state

- **GIVEN** a terminal success begins clearing a hard-key retry circuit
- **AND** the durable lookup fails
- **WHEN** the terminal cleanup settles
- **THEN** the proxy MUST NOT issue an unfenced durable clear
- **AND** the local admission guard MUST remain available on the clearing replica

### Requirement: Silent HTTP bridge sessions are quarantined from re-attach and reuse

When an HTTP bridge session proves silent/wedged, the proxy MUST quarantine its session key for a bounded window so later requests stop attaching to it. A session proves silent/wedged when either (a) a pending request being failed or retired carried a proxy-injected `previous_response_id`, had sent `response.create`, observed upstream response events, and never had `response.created` assigned, or (b) the session key hits two consecutive eventless `missing_response_created_timeout` retires. This holds for every path that fails or retires the request — partial stale-holder cleanup, the reader-failure funnel, and direct all-stale session retirement alike. The quarantine MUST be evaluated only when a request is already being failed or its session retired — never against a live owned turn — so a stream whose `response.created` was observed (including deferred-reasoning streams with long event gaps) MUST NOT be quarantined, and mere event silence during an owned live turn MUST NOT trigger quarantine by itself.

While a session key is quarantined: an existing session under that key MUST NOT be selected for reuse (a new request detaches it and proceeds on a fresh session), and for durable-anchor selection a quarantined session that is still open MUST count as absent, exactly as if it were already gone. The quarantine registry verdict is authoritative for the key: any session under the key while the quarantine window is active — including a freshly created replacement whose own completion has not yet cleared the quarantine — is equally excluded from reuse and equally absent for anchor selection. A fresh reattach whose incoming payload already looks like a full conversation resend MUST NOT receive a proxy-injected durable anchor through any injection point — the fresh-reattach injection, session-state hydration of the durable anchor, or the session-level injection — so the dispatch goes upstream genuinely unanchored with the client's own untrimmed payload. A payload that does not look like a full resend (a genuine delta-only continuation) MUST still receive the durable anchor, because it has no other way to convey prior conversation state.

Quarantine state MUST be bounded and self-recovering: it is in-memory and session-scoped, expires by TTL (a live session that outlives its quarantine window MUST become reusable again), is cleared when a response completes on the same session key only when the applicable clear fence authorizes it, and the quarantine marker and its detach decision MUST NOT mutate account health, account scoring, account eligibility, or durable ownership, or add a quarantine-specific health penalty. Detaching a quarantined session MAY release ordinary lifecycle resources, and the replacement request MUST use the existing normal account-selection path. A primary-key clear MUST be fenced by the quarantined session identity and canonical session registry, so a detached predecessor completion MUST NOT remove a newer replacement's quarantine entry. A recovery-origin key supplied by a stale-anchor completion MUST be fenced by the exact quarantine generation observed when that recovery was authorized, for both the distinct-key and same-key shapes; when no generation was observed, or the observed generation no longer matches, that completion MUST NOT clear the recovery-origin key.

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
- **WHEN** a response completes on that session key with its applicable session-identity or exact recovery-generation fence, or the quarantine TTL elapses
- **THEN** the quarantine (and its eventless strike counter) is cleared
- **AND** a completion without the required recovery-generation fence MUST leave a recovery-origin quarantine active
- **AND** a session that survived the quarantine window is reusable again instead of staying rejected forever
- **AND** no durable row, janitor work, or account-health write was involved at any point

#### Scenario: Detached predecessor cannot clear a replacement quarantine

- **GIVEN** a predecessor session quarantined a primary bridge key
- **AND** a replacement session reused that key and received a newer quarantine generation
- **WHEN** the detached predecessor completes and runs quarantine cleanup
- **THEN** the replacement's primary-key quarantine remains active
- **AND** the replacement generation remains authoritative

#### Scenario: A recovery that observed no quarantine cannot clear a raced one

- **GIVEN** a stale-anchor recovery observed no active quarantine on its recovery-origin key when it was authorized
- **AND** that key is quarantined while the retry is in flight
- **WHEN** the recovery completes and runs quarantine cleanup
- **THEN** the raced quarantine remains active
- **AND** this holds whether the recovery-origin key is a distinct key or the completing session's own key
