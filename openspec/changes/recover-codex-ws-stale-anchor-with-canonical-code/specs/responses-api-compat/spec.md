## MODIFIED Requirements

### Requirement: Codex WebSocket stale-anchor failures remain recoverable by a full-context retry
When serving or consuming the Codex-native `/backend-api/codex/responses` WebSocket route, upstream `previous_response_id` MUST be treated as an ephemeral optimization rather than durable conversation state. A stale-anchor continuity failure during a long-wait tool-output continuation MUST NOT hard-end the user turn before one full-context retry without `previous_response_id` has been attempted. The sanitized signal the service surfaces for a Codex-native stale-anchor failure MUST be the canonical `previous_response_not_found` error code, because that is the code an unmodified Codex client acts on to recover; the service MUST NOT substitute a proxy-specific classifier that standard clients do not recognize, and MUST NOT expose the raw upstream error envelope or the missing upstream response id.

#### Scenario: Long-running terminal wait invalidates the upstream previous response anchor
- **GIVEN** a Codex-native WebSocket session has completed a response with id `resp_old`
- **AND** the client later sends a `response.create` frame with `previous_response_id: "resp_old"` and tool-output or other delta input after a long idle period
- **WHEN** the upstream rejects `resp_old` with a stale-anchor error such as `previous_response_not_found`
- **THEN** the failure is classified as stale-anchor continuity loss
- **AND** the downstream signal uses `error.code = "previous_response_not_found"`, which an unmodified Codex client's built-in stale-anchor recovery retries once using full conversation history without `previous_response_id` before surfacing a turn-ending error
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

#### Scenario: Non-stale-anchor failures do not trigger full-context retry
- **WHEN** the upstream failure is quota, policy, auth, context-window, or another non-continuity error
- **THEN** the client MUST NOT convert it into a stale-anchor full-context retry
- **AND** codex-lb MUST preserve the original error class as much as safely possible

#### Scenario: ChatGPT backend omits param on an invalid previous response id
- **GIVEN** a request depends on a `previous_response_id`
- **WHEN** upstream returns `code = "invalid_request_error"` with the message `Invalid previous_response_id.`
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

#### Scenario: Verified owner-bound HTTP full resend drops only the rejected anchor
- **GIVEN** an HTTP-bridge continuation carries a prefix-verified, trim-safe full input history
- **AND** the retained request is not account-neutral because it contains account-bound tool or file history
- **WHEN** the continuity owner explicitly rejects its `previous_response_id` as not found before producing a response
- **THEN** codex-lb MUST remove the rejected anchor and replay the retained full request at most once on the same owner
- **AND** the replacement upstream request MUST NOT contain the rejected `previous_response_id`
- **AND** same-owner replay MUST use a unique owner-pinned internal key instead of bypassing the older hard-key retry circuit
- **AND** account-neutral replay admission MUST atomically claim the authorized original hard-key circuit generation
- **AND** local and durable retry-circuit state MUST NOT be deleted to authorize that replay
- **AND** successful completion of that verified replay MUST NOT clear the pre-existing local or durable circuit state
- **AND** successful completion MUST clear independent bridge quarantine state for both the replacement key and original hard key
- **AND** the request MUST NOT migrate to another account
- **AND** durable operation identity and settlement MUST remain attached to the replacement attempt
- **AND** a missing operation ledger, operation id, durable session id, owner epoch, or spool-reset capability MUST retain the anchor and fail closed
- **AND** delta-only or prefix-unverified requests MUST retain the existing fail-closed behavior
- **AND** an eventless transport failure without an explicit stale-anchor rejection MUST NOT by itself authorize this replay
- **AND** ordinary transport recovery without an explicit stale-anchor rejection MUST NOT bypass or clear the retry circuit
- **AND** an operation-journal recovery after an ambiguous transport failure MUST NOT remove the anchor or migrate accounts
- **AND** a request with a nonzero replay count MUST NOT dispatch another stale-anchor replacement
- **AND** a present blank or whitespace-only `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** a present non-string or null `param` MUST NOT be treated as an absent parameter for stale-anchor classification
- **AND** blank or whitespace-only parameter presence MUST survive every upstream event-normalization layer and MUST NOT be rewritten to the canonical continuity code
- **AND** the verified replacement MUST NOT receive a clean-close or other transport-level resend after its first dispatch
- **AND** account-neutral generation claim MUST apply only to the local/durable circuit generation observed when recovery was authorized
- **AND** a circuit generation that wins the durable compare-and-set first MUST suppress the replacement before submit
- **AND** generation claim MUST use the original hard session key even when account-neutral recovery creates a new soft key
- **AND** generation claim MUST run for absent, below-threshold, expired, half-open, and active circuit states
- **AND** generation claim MUST use a monotonic field independent from failure observation time so delayed clock-skewed failures remain mergeable
- **AND** verified replacement MUST NOT enter authentication replay
- **AND** HTTP-bridge terminal normalization MUST preserve a present empty parameter in the normalized error envelope
- **AND** inactive-owner UNKNOWN journal inspection MUST apply to both account-neutral and owner-bound verified full resends
- **AND** a pre-dispatch failure after rebinding an existing durable operation MUST restore that operation's failed fence and MUST NOT delete its row
- **AND** a durable operation snapshot MUST distinguish newly inserted rows from rebound rows
- **AND** generic eventless transport retry MUST NOT convert an anchored safe-fresh request into an unanchored replay without explicit stale-anchor rejection
- **AND** rebound rollback MUST restore the prior session/account/model/parent ownership fields
- **AND** a restored rebound operation MUST retain its durable identity and require a fresh rebind before any in-memory capacity or gate retry dispatches
- **AND** successful replacement completion MUST clear the original-key quarantine only when it still matches the generation observed at recovery authorization
- **AND** explicit stale-anchor rejection after any emitted response event or downstream-visible output MUST fail closed without anchored fallback dispatch
- **AND** same-owner verified replay MUST use a unique internal key pinned to the proven owner rather than bypassing the original hard-key circuit

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
- **AND** it does not expose the raw upstream error envelope or the missing previous response id

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

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable stale-anchor failure carrying the sanitized canonical `previous_response_not_found` code
- **AND** the downstream payload does not contain the raw upstream error envelope
- **AND** the downstream payload does not expose the missing previous response id
