# responses-api-compat Specification

## Purpose

Define Responses API compatibility contracts so Codex, OpenCode, and OpenAI-style clients preserve expected behavior.
## Requirements
### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

### Requirement: Responses requests reject uploaded input_image references

The system SHALL accept `{"type":"input_file","file_id":"file_*"}` attached-file items in `/v1/responses`, `/backend-api/codex/responses`, and `/responses/compact` request payloads and forward them verbatim.

When an `input_image` part contains a `file_id` field or an `image_url` starting with `sediment://`, the proxy MUST return HTTP 400 with `error.code = "unsupported_input_image_format"` and an explanation that the upstream Responses API only accepts inline `data:` URLs for `input_image`. The proxy MUST NOT fetch the upload, MUST NOT inline-convert the image, and MUST NOT trim, slim, or rewrite any conversation content.

`app/core/openai/requests.py::extract_input_image_file_references` MAY be used to detect the unsupported shape. This request path MUST NOT fetch uploads, inline-convert images, or otherwise reshape inbound conversation payloads.

#### Scenario: input_image file_id is rejected before forwarding

- **WHEN** a `/v1/responses` request contains `{"type":"input_image","file_id":"file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** the response explains that inline `data:` URLs are the supported `input_image` contract

#### Scenario: sediment upload URL is rejected before forwarding

- **WHEN** a `/responses/compact` request contains `{"type":"input_image","image_url":"sediment://file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** does not fetch or inline-convert the upload

#### Scenario: large request payload routes via HTTP transport on auto

- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge

- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Oversized responses request payloads fall back to HTTP
When `upstream_stream_transport` is `"auto"` and the serialized request payload size exceeds the WebSocket frame budget, the proxy MUST use upstream HTTP `POST` instead of WebSocket. If the HTTP responses bridge is enabled and the same oversized request would otherwise route through the bridge, the proxy MUST bypass the bridge for that request only and send it over raw HTTP. Explicit `upstream_stream_transport` overrides MUST still take precedence.

#### Scenario: large request payload routes via HTTP transport on auto
- **GIVEN** `upstream_stream_transport` is `"auto"` and the request payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large request payload bypasses the HTTP responses bridge
- **GIVEN** the HTTP responses bridge is enabled and the request payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Clean upstream close before any response event fails fast

When the HTTP responses bridge observes an upstream websocket close with `close_code = 1000` before any `response.*` event has been surfaced for the pending request, the proxy MUST classify the close as rejected input, surface HTTP 502 `upstream_rejected_input`, and MUST NOT trigger `retry_precreated` or `retry_fresh_upstream`.

#### Scenario: clean close before response.created is not retried

- **WHEN** upstream closes the HTTP responses bridge with `close_code = 1000` before any `response.*` event for the pending request
- **THEN** the proxy returns HTTP 502 with `error.code = "upstream_rejected_input"`
- **AND** does not transparently replay the pre-created request

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely.

#### Scenario: compact and stream watchdog defaults leave room for long turns
- **WHEN** the service starts with default configuration
- **THEN** `compact_request_budget_seconds` is at least 180 seconds
- **AND** `stream_idle_timeout_seconds` is at least 600 seconds

### Requirement: Upstream websocket drops penalize affected accounts
When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before signaling failure for those pending requests, except when the close carries a classified process-wide network failure. A classified process-wide network failure MUST remain account neutral and use its network error code. For other closes, the proxy MUST surface `stream_incomplete` to affected pending requests except when a direct Responses WebSocket request has already successfully emitted a finite integer `sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST record the request outcome as `stream_incomplete` without emitting a synthetic terminal frame under the active response id, then MUST close the downstream WebSocket with code 1011.

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

### Requirement: Single HTTP bridge previous-response misses recover or fail closed
When an HTTP bridge session receives an anonymous upstream `previous_response_not_found` error for a single pending follow-up request, the service MUST treat the error as an internal continuity-loss signal. It MUST either recover through the existing previous-response rebind path or rewrite the error to a retryable continuity failure instead of forwarding the raw upstream invalid-request error.

#### Scenario: single pending HTTP bridge follow-up loses previous-response continuity
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has exactly one pending request with `previous_response_id`
- **AND** upstream emits `previous_response_not_found` without a `response.id`
- **THEN** the service attempts the existing previous-response recovery path
- **AND** if recovery is unavailable, it emits a retryable continuity failure for that request
- **AND** the downstream error code is not `previous_response_not_found`

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error. A payload that only carries incremental tool outputs for tool calls that are not also present in the same request is not self-contained and MUST NOT be replayed as a fresh turn without `previous_response_id`.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items without their matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without `previous_response_id`
- **AND** the downstream client receives a retryable continuity failure rather than a fabricated fresh turn

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped `previous_response_not_found` error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events
When serving streaming `POST /v1/responses`, the service MUST emit only event types defined by the OpenAI Responses SSE contract (the `response.*` and `error` families) on the public stream. The service MUST drop any vendor-internal event types — specifically, any event whose `type` begins with `codex.` (for example `codex.rate_limits`) — before they reach the public stream. The `/backend-api/codex/*` routes are NOT subject to this requirement and MUST continue forwarding these events unchanged.

#### Scenario: Codex-internal rate-limit event is dropped before response.created
- **WHEN** the upstream Codex backend emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain the `codex.rate_limits` event
- **AND** the first event the public stream emits MUST be `response.created`

#### Scenario: Codex-internal events on the Codex CLI route are preserved
- **WHEN** the upstream emits `codex.rate_limits` for a `POST /backend-api/codex/responses` request
- **THEN** the response stream forwards the `codex.rate_limits` event to the Codex CLI client unchanged

### Requirement: Streamed /v1 responses terminal output is backfilled from item events
When serving streaming `POST /v1/responses`, if the upstream's terminal `response.completed` or `response.incomplete` event carries `output` as missing or as an empty list, the service MUST reconstruct `output` from the `response.output_item.done` events emitted earlier in the same stream before yielding the terminal SSE event. The reconstructed `output` MUST preserve the `output_index` ordering and the raw item payloads. When the terminal `response.completed` / `response.incomplete` already carries a non-empty `output`, the service MUST forward it unchanged.

#### Scenario: Terminal response.completed with empty output is backfilled from streamed items
- **GIVEN** the upstream emits `response.output_item.done` events with valid message or function-call items
- **WHEN** the upstream's terminal `response.completed` event carries `output: []`
- **THEN** the public stream's terminal `response.completed` event MUST carry the reconstructed `output` array, populated from the streamed `output_item.done` items in `output_index` order
- **AND** an OpenAI Python SDK consumer calling `stream.get_final_response().output` MUST receive the same populated list

#### Scenario: Terminal response.completed already carries output
- **WHEN** the upstream's terminal `response.completed` event already includes a non-empty `output` array
- **THEN** the public stream's terminal event MUST carry that `output` array unchanged

### Requirement: Public /v1 responses SSE stream starts with response.created
When serving streaming `POST /v1/responses`, the first OpenAI-contract event the public stream emits MUST be `response.created`. When the upstream's first standard `response.*` event is not `response.created` (for example when the Codex backend jumps directly to `response.failed` on upstream rejection mid-stream), the service MUST synthesize a `response.created` SSE event from the source event's `response` envelope and emit it before forwarding the source event, so that consumers using the OpenAI Python SDK's `responses.stream(...)` parser do not raise `RuntimeError`.

#### Scenario: Upstream error stream that skips response.created is repaired
- **WHEN** the upstream's first standard event is `response.failed` (no preceding `response.created`)
- **THEN** the public stream MUST emit a synthesized `response.created` event derived from the failed event's `response` envelope before forwarding the `response.failed` event
- **AND** an OpenAI Python SDK consumer iterating the stream MUST NOT raise `RuntimeError` from the parser's initial-response check

#### Scenario: Normal stream is not double-emitted
- **WHEN** the upstream's first standard event is already `response.created`
- **THEN** the public stream MUST emit exactly one `response.created` event (no synthesized duplicate)

### Requirement: Upstream overload envelopes are classified as retryable transient failures

When `classify_upstream_failure` observes an upstream error envelope whose `code` is `overloaded_error` or `server_is_overloaded`, the system MUST treat it as `retryable_transient` regardless of the accompanying HTTP status. Streamed Responses API traffic can deliver the overload envelope on a connection that has already returned HTTP 200, so a 5xx-only heuristic is insufficient to drive account fail-over and bounded retry.

#### Scenario: `overloaded_error` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the failover layer is eligible to retry the request or fail over to another account instead of returning a non-retryable error to the client

#### Scenario: `overloaded_error` with a 5xx status remains retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` is 500, 502, 503, or 504
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the result is the same as the no-status path, so the 5xx fallback heuristic is not the only signal driving the decision

#### Scenario: `server_is_overloaded` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="server_is_overloaded"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the streaming retry layer is eligible to retry the request before surfacing the terminal overload event

#### Scenario: HTTP bridge retries a pre-created overload event

- **GIVEN** the HTTP responses session bridge is enabled
- **WHEN** the first upstream `response.failed` or `error` event has `code="overloaded_error"` or `code="server_is_overloaded"`
- **THEN** the bridge MUST retry the pre-created request before forwarding that terminal event
- **AND** the bridge MUST preserve its existing no-replay behavior after downstream-visible output or for other fail-fast error codes

### Requirement: Strict function tool parameter schemas are pre-validated

The service MUST pre-validate the JSON schema attached to a function tool when that tool sets `strict: true`, before opening any upstream connection. The validation rules mirror OpenAI's Structured Outputs strict-mode policy (https://platform.openai.com/docs/guides/structured-outputs) and the existing `enforce_strict_text_format` policy for `text.format.json_schema`:

- Every `object` schema node MUST set `additionalProperties: false`.
- Every property under `properties` MUST appear in `required`.
- Every schema node MUST carry a `type` key (no empty `{}` schemas).
- The same rules apply recursively to nested object / array / combinator (`anyOf` / `oneOf` / `allOf`) schemas.

When any of those rules is violated, the service MUST reject the request with `HTTP 400 invalid_request_error` carrying:

- `error.code = "invalid_function_parameters"`
- `error.message = "Invalid schema for function '<name>': In context=<path>, <reason>."`
- `error.param = "tools[<index>].parameters"` for native Responses-API requests; `error.param = "tools[<index>].function.parameters"` for chat-completions requests routed through the coercion pipeline.

This brings strict function tool schema handling into parity with `text.format.json_schema`. Without it, an invalid strict tool schema reaches the upstream Codex backend, which closes the WebSocket with `close_code=1000` and surfaces as a generic `502 server_error / upstream_rejected_input`. Real OpenAI returns `400 invalid_function_parameters` for the identical payload. A 5xx on a deterministically-broken request also triggers retry / failover loops in well-behaved clients.

#### Scenario: Strict tool missing `additionalProperties` is rejected with 400

- **WHEN** a client sends `tools: [{"type": "function", "name": "f", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, "strict": true}]`
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"`, `error.message` matching `/Invalid schema for function 'f': In context=\(\), 'additionalProperties' is required to be supplied and to be false\./`, and `error.param = "tools[0].parameters"`

#### Scenario: Strict tool with `additionalProperties: true` is rejected

- **WHEN** a client sends a function tool with `strict: true` and `parameters.additionalProperties = true`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the same `'additionalProperties' is required to be supplied and to be false` message

#### Scenario: Strict tool with property missing from `required` is rejected

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, but `required` omits one of the listed `properties`
- **THEN** the proxy returns `HTTP 400 invalid_function_parameters` with the `'required' is required to be supplied and to be an array including every key in properties` message

#### Scenario: Compliant strict tool is accepted

- **WHEN** a client sends a function tool with `strict: true`, `additionalProperties: false`, and every property listed in `required`
- **THEN** the proxy forwards the request to the upstream unchanged and the response is `200`

#### Scenario: `strict: false` or omitted strict skips pre-validation

- **WHEN** a client sends a function tool with `strict: false` or without a `strict` key, and the schema would have violated strict mode (e.g. missing `additionalProperties`)
- **THEN** the proxy does not run the strict pre-validation and forwards the request unchanged, matching pre-fix behavior for non-strict tools

### Requirement: Same-response side-effect tool-call replays are suppressed

When the proxy receives multiple downstream `response.output_item.done` events for the same response that describe the same side-effecting local tool operation, the proxy SHALL forward only the first event to the client.

The proxy SHALL treat `exec_command`, `write_stdin`, `multi_tool_use.parallel`, and `apply_patch_call` events as side-effecting. For these tools, a changed `call_id` alone MUST NOT make a same-response replay distinct.

When a `multi_tool_use.parallel` event contains duplicate nested side-effect operations, the proxy SHALL remove the duplicate nested operations before forwarding the event. Duplicate nested `exec_command` operations MUST ignore volatile output/wait fields such as `yield_time_ms` and `max_output_tokens`. Duplicate nested `write_stdin` operations MUST be scoped by `session_id` and `chars`. Duplicate nested `wait_agent` operations MUST be scoped by the target set.

Read-only function calls and matching operations under different response ids MUST continue to pass through.

#### Scenario: side-effect call replay uses a new call id

- **WHEN** a streamed response emits two `exec_command` output items with the same response id and arguments but different call ids
- **THEN** the proxy forwards the first event
- **AND** suppresses the second event

#### Scenario: read-only call ids stay distinct

- **WHEN** a streamed response emits two read-only function calls with the same arguments and different call ids
- **THEN** the proxy forwards both events

#### Scenario: later response ids stay distinct

- **WHEN** two responses emit the same side-effecting operation under different response ids
- **THEN** the proxy forwards both events

#### Scenario: parallel batch contains duplicate shell operations

- **WHEN** a `multi_tool_use.parallel` event contains two nested `functions.exec_command` operations with the same command and only different wait/output fields
- **THEN** the proxy forwards one nested operation inside the parallel batch
- **AND** does not forward the duplicate nested operation to the client

### Requirement: Continuity-dependent Responses follow-ups fail closed with retryable errors
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps. When forwarding a turn-state-anchored follow-up to its bridge owner fails with `bridge_owner_unreachable` and a fresh durable lookup shows the owner no longer holds an active lease (released, expired, or the row is missing or CLOSED), the service MUST recover the follow-up locally through durable takeover instead of returning the retryable error. The fresh durable lookup MUST use the same resolution semantics as request routing, including the latest-turn-state fallback, so a row originally resolved without a registered alias remains takeover-eligible. When the durable lease is still actively held by another instance — including DRAINING rows whose lease has not been released or expired — the service MUST keep failing closed with the retryable error.

#### Scenario: HTTP bridge loses local continuity metadata for a follow-up request
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` follow-up request depends on `previous_response_id` or a hard continuity turn-state
- **AND** the bridge cannot reconstruct the matching live continuity state from local or durable metadata
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: in-flight bridge follower loses continuity while waiting on the same canonical session
- **WHEN** a follow-up request waits on an in-flight HTTP bridge session for the same hard continuity key
- **AND** the bridge still cannot reconstruct safe continuity state once the leader finishes
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: multiplexed follow-ups fail closed only for the matching continuity anchor
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests with different `previous_response_id` anchors
- **AND** continuity loss is detected for exactly one of those anchors
- **THEN** the service applies the retryable fail-closed continuity error only to the matching follow-up request
- **AND** it does not expose raw `previous_response_not_found`
- **AND** unrelated pending requests continue on their own response lifecycle

#### Scenario: multiplexed follow-ups sharing one anchor fail closed together without leaking raw continuity errors
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests that share the same `previous_response_id` anchor
- **AND** upstream emits an anonymous continuity loss event such as `previous_response_not_found` for that shared anchor
- **THEN** the service rewrites each affected follow-up into a retryable continuity error
- **AND** no affected follow-up exposes raw `previous_response_not_found`
- **AND** the run remains usable for subsequent requests after the rewritten failures

#### Scenario: single pre-created follow-up still fails closed when continuity loss omits explicit response id in message
- **WHEN** a websocket follow-up request is pending with `previous_response_id` and has not received a stable upstream `response.id` yet
- **AND** upstream emits `previous_response_not_found` with `param=previous_response_id`
- **AND** the upstream error message omits the literal previous response identifier
- **THEN** the service still maps that continuity loss to the pending follow-up
- **AND** it rewrites the downstream terminal event to a retryable continuity error
- **AND** it does not surface raw `previous_response_not_found` to the client

#### Scenario: turn-state follow-up recovers locally after the owner released its lease
- **WHEN** a turn-state-anchored follow-up without `previous_response_id` is forwarded to its bridge owner during the post-shutdown ring grace window
- **AND** the forward fails with `bridge_owner_unreachable`
- **AND** a fresh durable lookup using the request-routing resolution semantics (registered alias or latest-turn-state fallback) shows the lease is released or expired
- **THEN** the service retries the follow-up locally through durable takeover instead of returning the retryable 503
- **AND** the takeover retry carries the fresh durable lookup as its continuity anchor even when the turn-state alias registration was lost
- **AND** a fresh durable lookup showing a live lease held by another instance — even for a DRAINING row — still fails closed with the retryable `bridge_owner_unreachable` error

### Requirement: Hard continuity owner lookup fails closed

When a request depends on hard continuity ownership, the service MUST fail
closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT
continue with account selection that bypasses hard owner enforcement. A direct
WebSocket continuation already attached to its required open owner socket MUST
NOT be failed solely because a new per-turn selection attempt temporarily
excludes that owner.

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

### Requirement: Request logs persist requested, actual, and billable service tiers separately
For Responses proxy traffic, the system MUST persist the operator-requested tier, the upstream-reported actual tier when available, and the effective billable tier used for pricing as separate request-log fields.

The legacy `fast` alias MUST be normalized to the canonical upstream value
`priority` before forwarding and before it is stored as the requested tier.
The upstream-reported `response.service_tier`, when present, remains the
authoritative actual tier even when it differs from the requested tier.

#### Scenario: Upstream reports a downgraded actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Fast alias is logged as a priority request
- **WHEN** a client sends a Responses request with `service_tier: "fast"`
- **AND** the upstream response later reports `service_tier: "default"`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = "default"`
- **AND** the persisted request log entry records billable `service_tier = "default"`

#### Scenario: Upstream omits the actual tier
- **WHEN** a client sends a Responses request with `service_tier: "priority"`
- **AND** the upstream response omits `service_tier`
- **THEN** the persisted request log entry records `requested_service_tier = "priority"`
- **AND** the persisted request log entry records `actual_service_tier = null`
- **AND** the persisted request log entry records billable `service_tier = "priority"`

### Requirement: API key service tier enforcement applies to upstream Responses requests

When an API key carries an enforced service tier, the proxy MUST override any incoming Responses request service tier with that enforced value before forwarding upstream. The legacy alias `fast` MUST be treated as `priority`.

#### Scenario: Enforced service tier overrides the request payload

- **WHEN** an API key is configured with `enforcedServiceTier: "priority"`
- **AND** an incoming Responses request asks for `service_tier: "default"`
- **THEN** the forwarded upstream payload uses `service_tier: "priority"`

#### Scenario: Fast alias is applied as priority

- **WHEN** an API key is configured with `enforcedServiceTier: "fast"`
- **THEN** the forwarded upstream payload uses the canonical value `priority`

### Requirement: Cursor GPT-5 model aliases normalize to canonical slugs

For Responses proxy traffic, the service MUST recognize Cursor-style GPT-5 model aliases formed by appending known suffix tokens
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs, including the GPT-5.6
personality slugs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged; `ultra` and `max` are not suffix tokens (they are not effort levels
every GPT-5-family base supports — `gpt-5.6-luna` advertises no `ultra`), so
labels such as `gpt-5.6-sol-ultra` pass through unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 personality alias normalizes reasoning and service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-extra-high-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.6-sol"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

#### Scenario: GPT-5.6 ultra-suffixed label is not rewritten

- **WHEN** a client sends a Responses request with `model: "gpt-5.6-sol-ultra"`
- **THEN** the forwarded upstream request keeps `model: "gpt-5.6-sol-ultra"` unchanged

### Requirement: OpenAI-compatible Responses payload sanitation removes provider-specific thinking aliases

The shared OpenAI-compatible Responses sanitation path MUST normalize third-party thinking aliases into the canonical `reasoning` object before upstream forwarding. Unknown provider-specific thinking controls MUST NOT be passed through unchanged to the upstream ChatGPT backend.

#### Scenario: Shared payload sanitation maps enable_thinking

- **WHEN** an internal Responses payload contains `enable_thinking: true`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Explicit reasoning wins over provider aliases

- **WHEN** an internal Responses payload contains both `reasoning: {"effort":"high"}` and `thinking: {"type":"enabled"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "high"`
- **AND** the forwarded upstream payload does not include `thinking`

### Requirement: Public Responses streams expose renderable final text
For OpenAI-style streaming `/v1/responses` and `/backend-api/codex/responses`, the service MUST expose renderable `response.output_text.delta` events for assistant message text when upstream provides final text only in output item or terminal response output payloads. The service MUST NOT duplicate text deltas for an output item that already emitted a text delta.

#### Scenario: final output item text is exposed as a text delta
- **WHEN** upstream emits a `response.output_item.done` event with assistant message text and no prior text delta for that output item
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the final item event

#### Scenario: terminal response output text is exposed as a text delta
- **WHEN** upstream emits only a terminal `response.completed` event with assistant message text in `response.output`
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the terminal event

#### Scenario: existing text deltas are preserved without duplication
- **WHEN** upstream already emits a `response.output_text.delta` for an output item
- **THEN** the service forwards the stream without synthesizing another text delta for that same output item

### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

#### Scenario: Chat Completions tool arguments avoid snapshot duplication
- **WHEN** `/v1/chat/completions` maps Responses tool-call events that include incremental deltas and later finalized snapshots for the same tool call
- **THEN** the final `tool_calls[].function.arguments` value is exactly one valid JSON string for that tool call
- **AND** the adapter MUST NOT append full snapshot payloads on top of already-collected incremental argument deltas

#### Scenario: Parallel tool calls route arguments by output_index
- **WHEN** `/v1/chat/completions` maps Responses events for two or more parallel function calls
- **THEN** the adapter MUST route each event to its `tool_calls[]` slot using the event's `output_index` as the primary routing key
- **AND** the adapter MUST preserve a stable mapping from `output_index` to the same slot across `output_item.added`, `output_item.done`, `response.function_call_arguments.delta`, and `response.function_call_arguments.done` events for that call
- **AND** parallel tool calls MUST NOT collapse to index `0` when their argument-only events identify the owning call only via `item_id`

#### Scenario: Parallel tool calls also resolve through item_id aliases
- **WHEN** an `output_item.added` or `output_item.done` event exposes both `item.id` (e.g. `"fc_..."`) and `item.call_id` (e.g. `"call_..."`)
- **THEN** the adapter MUST register `item.id` as an alias to the same `tool_calls[]` slot as the `call_id`
- **AND** subsequent argument-only events that carry only `item_id` MUST resolve to that aliased slot, even if their `output_index` has not yet been observed

#### Scenario: Internal item_id never leaks into the public call identifier
- **WHEN** the adapter exposes a tool call to the client as `tool_calls[].id` or `tool_calls[].call_id`
- **THEN** the value MUST be the upstream `call_...` identifier and MUST NOT be substituted with the internal `fc_...` item id used solely for routing

### Requirement: Responses routing prefers budget-safe accounts
When serving Responses routes, the service MUST prefer eligible accounts that are still below the configured budget threshold over eligible accounts already above that threshold. If no below-threshold candidate exists, the service MAY fall back to the pressured candidates.

#### Scenario: Fresh Responses request avoids a near-exhausted account
- **WHEN** `/backend-api/codex/responses`, `/backend-api/codex/responses/compact`, `/v1/responses`, or `/v1/responses/compact` selects among multiple eligible active accounts
- **AND** one candidate is above the configured budget threshold
- **AND** another candidate remains below that threshold
- **THEN** the below-threshold candidate is chosen first

### Requirement: Upstream Responses event size budget
The service SHALL allow upstream Responses SSE events and upstream websocket message frames up to 16 MiB by default before treating them as oversized.

#### Scenario: built-in tool output exceeds the old 2 MiB limit
- **WHEN** upstream Responses traffic includes a single SSE event or websocket message frame larger than 2 MiB but not larger than 16 MiB
- **THEN** the proxy continues processing the event instead of closing the upstream websocket locally with `1009 message too big`

### Requirement: Upstream Responses transport strategy
For streaming Codex/Responses proxy requests, the system MUST let operators choose the upstream transport strategy through dashboard settings. The resolved strategy MAY be `auto`, `http`, or `websocket`, and `default` MUST defer to the server configuration default.

#### Scenario: Dashboard forces websocket upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"websocket"`
- **THEN** streaming Responses requests use the upstream websocket transport

#### Scenario: Dashboard forces HTTP upstream transport
- **WHEN** the dashboard setting `upstream_stream_transport` is set to `"http"`
- **THEN** streaming Responses requests use the upstream HTTP/SSE transport

#### Scenario: Auto transport falls back when websocket upgrades are rejected
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** auto selection chose the websocket transport
- **AND** the upstream rejects the websocket upgrade with HTTP `426`
- **THEN** the proxy retries the request over the upstream HTTP/SSE transport

#### Scenario: Session affinity alone does not trigger websocket upstream transport
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** a request includes a `session_id`
- **AND** it does not include an allowlisted native Codex `originator` or explicit Codex websocket feature headers
- **THEN** the auto strategy MUST keep using the existing model-preference transport selection rules

#### Scenario: Auto transport honors websocket-preferred bootstrap models before registry warmup
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the model registry has not loaded a snapshot yet
- **AND** the request targets a locally bootstrapped websocket-preferred model family such as `gpt-5.4` or `gpt-5.4-*`
- **AND** the request does not include the built-in `image_generation` tool
- **THEN** the proxy chooses the upstream websocket transport

#### Scenario: Auto transport prefers HTTP for image-generation tool requests
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the request includes a built-in `image_generation` tool
- **THEN** the proxy chooses the upstream HTTP/SSE transport even if the model would otherwise prefer websocket

#### Scenario: Legacy settings preserve the pre-feature default
- **WHEN** transport selection runs against a legacy settings object that does not expose the newer upstream transport fields
- **THEN** the proxy MUST preserve the pre-feature HTTP transport default for model-preference auto-selection unless an explicit legacy websocket mode or native Codex websocket signal opts in

### Requirement: Responses-compatible tool payload handling
The service SHALL accept built-in Responses tool definitions on `/backend-api/codex/responses` and `/v1/responses` without locally rejecting them. The service MAY normalize documented aliases, but upstream model/tool compatibility validation MUST remain the upstream contract.

#### Scenario: full Responses request includes built-in tools
- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with built-in Responses tools such as `image_generation`, `computer_use`, `computer_use_preview`, `file_search`, or `code_interpreter`
- **THEN** the proxy forwards those tool objects upstream instead of returning a local `invalid_request_error`

### Requirement: Compact requests drop tool-only fields
The service SHALL remove `tools` and `tool_choice` from compact request payloads, and set `parallel_tool_calls` to `false`, before calling the upstream compact endpoint.

#### Scenario: compact request reuses a full Responses payload shape

- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `tools`, `tool_choice`, or `parallel_tool_calls`
- **THEN** the proxy drops `tools` and `tool_choice` before the upstream compact request
- **AND** the proxy sends `parallel_tool_calls` as `false`
- **AND** the compact request continues without a local or upstream `invalid_request_error` caused by `param="tools"`

### Requirement: Responses requests accept input_file content items with a file_id

The system SHALL accept `input_file` content items that reference an upload by `file_id` in `/backend-api/codex/responses` and `/v1/responses` request payloads (both list-form and string-form `input`). These items MUST be forwarded to upstream verbatim. The same MUST apply to `/responses/compact` request bodies. The proxy MUST NOT raise `input_file.file_id is not supported` for these items.

#### Scenario: input_file with file_id is accepted in a /responses request

- **WHEN** a client posts a `/v1/responses` request whose `input` contains a `{"type": "input_file", "file_id": "file_abc"}` content item
- **THEN** the request validates and the upstream payload includes that content item unchanged

#### Scenario: input_file with file_id is accepted in a compact request

- **WHEN** a client posts a `/responses/compact` request whose `input` contains an `input_file` item with a `file_id`
- **THEN** the request validates and is forwarded to upstream verbatim

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files` when an in-memory pin for that `file_id` is still live. A live file pin is hard ownership evidence: it MUST override prompt-cache or bare process-session locality and MUST agree with independently resolved turn-state, previous-response, bridge, or other hard ownership.

When multiple `file_id`s are referenced, all live pins MUST resolve to the same account. If at least one ID has a live pin and another ID has no live pin, the request MUST fail with `file_owner_unavailable`; if live pins resolve to different accounts, it MUST fail with `continuity_owner_conflict`. If none of the referenced IDs has a live pin, the proxy MUST preserve compatibility with files registered directly upstream or before the current process observed the upload by forwarding the opaque IDs verbatim under ordinary unpinned routing.

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a`
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}`
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: file_id pin overrides prompt-cache locality

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST route to `account_a` and MUST NOT send the account-scoped file to the prompt-cache account

#### Scenario: opaque file_id without a live pin remains compatible

- **GIVEN** a request references a `file_id` registered directly upstream or before the current process observed its upload
- **AND** no referenced file has a live in-memory pin
- **WHEN** the request is routed
- **THEN** the proxy MUST forward the `file_id` verbatim under ordinary unpinned routing
- **AND** it MUST NOT reject the request solely because local owner metadata is absent

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty accepted session header, the service MUST use that value as the routing affinity key for upstream account selection unless the client supplied a non-empty `x-codex-turn-state` header. If the request lacks a client-supplied `prompt_cache_key`, the service MUST derive and attach a stable `prompt_cache_key` before upstream forwarding so account affinity and upstream prompt-cache routing can coexist. Accepted session headers are `session_id`, `session-id`, `x-codex-session-id`, `x-codex-conversation-id`, and `thread-id`, in that priority order.

A turn state synthesized by the proxy for the current downstream WebSocket handshake MUST NOT override a client-supplied session header or prompt-cache key for routing or WebSocket continuity selection. The proxy MUST seed WebSocket continuity storage under that synthesized turn state so a later client echo can reuse the completed-turn owner. The proxy MUST continue to forward that synthesized turn state upstream. A turn state sent by the client, including one that the proxy generated and the client later echoed, remains a client-supplied turn-state affinity key.

When a WebSocket handshake has neither a client-supplied turn state nor an accepted session header, the proxy MUST store its generated turn state as the WebSocket continuity key. A later connection that echoes that accepted value MUST recover the same continuity state.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing
- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `prompt_cache_key`
- **THEN** the routing decision still uses durable `codex_session` affinity for account selection
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: backend WebSocket reconnect retains session affinity despite a generated turn state
- **WHEN** two backend Codex Responses WebSocket connections include the same accepted session header and omit `x-codex-turn-state`
- **AND** the proxy generates a distinct turn state for each handshake
- **THEN** both account selections use the session header as the durable `codex_session` affinity key
- **AND** each generated turn state is still forwarded to the upstream

#### Scenario: echoed generated turn state remains a client continuation key
- **WHEN** a client reconnects with a non-empty `x-codex-turn-state` value it received from an earlier proxy handshake
- **THEN** that turn state remains the routing and WebSocket continuity key ahead of a broader accepted session header
- **AND** full-resend continuity for that echoed turn state can reuse the earlier completed response anchor

#### Scenario: generated turn state seeds continuity without a session header
- **WHEN** a backend Codex Responses WebSocket handshake omits both an accepted session header and `x-codex-turn-state`
- **AND** the proxy generates and returns a turn state for that handshake
- **THEN** the proxy stores its WebSocket continuity state under that generated value
- **AND WHEN** a later connection sends that value in `x-codex-turn-state`
- **THEN** it recovers the stored continuity state

### Requirement: Proxy-generated prompt cache key derivation is operator-toggleable
The service MUST provide a runtime flag that disables only proxy-generated prompt-cache-key derivation. When disabled, the service MUST continue forwarding any client-supplied `prompt_cache_key` unchanged and MUST NOT synthesize a new one.

#### Scenario: Derivation disabled preserves client-supplied key
- **WHEN** the derivation flag is disabled and a client sends `prompt_cache_key`
- **THEN** the service forwards that key unchanged
- **AND** it does not generate a replacement key

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key instead of opening a brand new upstream session for every eligible request. The bridge key MUST use an explicit session/conversation header when present; otherwise it MUST use normalized `prompt_cache_key`, and when the client omits `prompt_cache_key` the service MUST derive a stable key from the same cache-affinity inputs already used for OpenAI prompt-cache routing. While bridged, the service MUST preserve the external HTTP/SSE contract, MUST continue request logging with `transport = "http"`, and MUST keep requests from different bridge keys isolated from one another.

#### Scenario: bridge forwards hard continuity keys to the owner replica
- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state` or an explicit session header
- **AND** that request lands on a non-owner instance
- **THEN** the service MUST forward the request internally to the owner replica
- **AND** it MUST NOT return a topology-bearing `bridge_instance_mismatch` error to the client for that owner mismatch alone

#### Scenario: gateway-style prompt-cache bridge requests tolerate wrong-replica arrival
- **WHEN** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST treat the owner mismatch as a locality miss instead of a continuity failure

#### Scenario: forwarded bridge requests fail closed when owner forwarding loops
- **WHEN** a forwarded hard-continuity bridge request reaches another non-owner replica
- **THEN** the service MUST fail the request with a generic 5xx bridge-forward error
- **AND** it MUST NOT attempt another owner handoff

#### Scenario: local restart orphan is recovered by the replacement instance
- **WHEN** a single local bridge instance is replaced while durable hard-continuity ownership still references the old instance id
- **AND** the old owner has no distinct active forwarding endpoint from the current replacement instance
- **THEN** the replacement instance MUST treat the row as restart-orphaned and may claim durable ownership locally
- **AND** same-account takeover MUST preserve the latest persisted response anchor until a replacement response id is recorded
- **AND** normal client retries MUST NOT be stranded waiting for the old instance lease to expire

When request aliases resolve to different durable rows for the same account,
an explicitly requested previous-response alias MUST select its row even if
that row has since advanced to a newer response id. Without an explicitly
resolved previous-response alias, recovery MUST select the freshest row that
contains a persisted response anchor rather than using alias enumeration order.

#### Scenario: requested durable response alias survives same-account row divergence

- **GIVEN** turn-state and previous-response aliases resolve to different durable rows for the same account
- **AND** the request names the previous-response alias whose row has since advanced to a newer response id
- **WHEN** the service resolves durable continuity
- **THEN** it selects the row resolved by the requested previous-response alias
- **AND** it preserves that row's latest persisted response anchor

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate in-process account pressure in addition to persisted usage. Account selection MUST account for in-flight response-create work, active streams, leased token/cost estimates, recent selection pressure, account health, and configured account-local caps. Selection and lease acquisition MUST be atomic with respect to other in-process selections, and the critical section MUST NOT perform database calls, network calls, sleeps, or other blocking I/O.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight pressure and caps
- **AND** one account does not receive all requests solely because persisted usage was stale

#### Scenario: File-pinned bridge request does not reroute under local pressure

- **GIVEN** an HTTP bridge `/v1/responses` request references an `input_file.file_id` pinned to an upstream account
- **AND** that owner account or bridge session rejects admission with local pressure before output starts
- **WHEN** the proxy handles the admission failure
- **THEN** it returns the owner account overload instead of soft-rerouting the payload to another account
- **AND** the file-scoped request is not replayed to an account that does not own the file

#### Scenario: Runtime lock excludes blocking I/O

- **WHEN** account selection holds the balancer runtime lock
- **THEN** the implementation performs only in-memory scoring and lease mutation
- **AND** database, network, sleep, or bridge queue waits happen outside that lock

### Requirement: Account leases release on all terminal paths

Every account-local lease acquired for a Responses request MUST be idempotently released or settled on success, upstream error, local startup error, bridge submit failure, startup probe conversion, non-streaming collect completion, failover, downstream disconnect, cancellation, timeout, and retry. A bounded stale-lease watchdog MUST reclaim leases that survive unexpected task cancellation or exceptions, and stale reclamation MUST emit warning/metric evidence. Leases MUST NOT be persisted to the database.

#### Scenario: Lease releases after downstream disconnect

- **WHEN** a streaming `/v1/responses` client disconnects before a terminal upstream event
- **THEN** the account stream lease is released exactly once
- **AND** later routing pressure no longer includes that stream

#### Scenario: WebSocket local account cap releases API-key reservation

- **GIVEN** a WebSocket `response.create` has reserved API-key usage
- **AND** account-local response-create lease acquisition fails with `account_response_create_cap`
- **WHEN** the proxy emits the local terminal failure
- **THEN** the API-key usage reservation is released
- **AND** the pending request is removed from websocket local state

#### Scenario: Stale watchdog recovers orphaned lease

- **WHEN** a request task exits unexpectedly after acquiring an account lease
- **AND** the lease exceeds the configured TTL
- **THEN** the watchdog releases the stale lease
- **AND** emits a low-cardinality warning/metric

#### Scenario: Active stream lease is not reclaimed before valid stream budget

- **GIVEN** a stream lease is older than the base lease TTL
- **AND** the configured Responses stream or HTTP bridge request budget has not elapsed
- **WHEN** account lease stale reclamation runs
- **THEN** the stream lease still counts against account-local stream pressure
- **AND** the proxy does not admit extra streams over the account stream cap by age alone

### Requirement: Public Responses streaming is proxy-timeout friendly

Streaming `/v1/responses` responses MUST include anti-buffering/cache headers suitable for SSE through common front-door proxies and MUST emit an early flushable SSE comment or event before long upstream startup waits can appear idle. Periodic SSE keepalive behavior MUST continue while waiting for upstream events. These heartbeat comments MUST NOT violate the public Responses event contract: OpenAI-contract events still begin with `response.created` when event parsing ignores comments.

#### Scenario: Streaming response includes anti-buffering headers

- **WHEN** a client starts streaming `POST /v1/responses`
- **THEN** the response headers include SSE content type and anti-buffering/cache directives
- **AND** the headers are present before upstream response completion

#### Scenario: Early heartbeat precedes long upstream silence

- **WHEN** upstream startup takes longer than the heartbeat interval
- **THEN** the client receives a flushable SSE heartbeat before a front-door origin idle timeout would trigger
- **AND** the first OpenAI-contract event remains `response.created` when upstream accepts the request

### Requirement: Codex WebSocket top-level previous-response errors are masked
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy MUST treat upstream `type: "error"` frames with top-level error fields as upstream error envelopes if the frame does not contain a nested `error` object. If those fields describe a `previous_response_not_found` continuity miss, the proxy MUST use the existing continuity fail-closed behavior and MUST NOT forward raw `previous_response_not_found` or the missing response id to the downstream Codex client.

#### Scenario: ChatGPT backend emits top-level previous-response miss on Codex websocket
- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** the ChatGPT backend emits `{"type":"error","code":"previous_response_not_found","param":"previous_response_id",...}` without a nested `error` object
- **THEN** the downstream event is a retryable continuity failure such as `stream_incomplete`
- **AND** the downstream payload does not contain `previous_response_not_found`
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Equal idle and request-budget stream deadlines preserve idle classification
When the configured upstream stream idle timeout is equal to the proxy request budget, and an already-started streaming Responses body has had no upstream activity for the full shared window, the system MUST classify the timeout as `stream_idle_timeout` even if scheduler jitter observes the deadline after it has elapsed. When the request budget is strictly shorter than the stream idle timeout, when the generic total timeout fires before an upstream response has started, when the remaining request budget for the next read is shorter than a fresh idle window, or when a generic total timeout follows recent upstream body activity, the system MUST continue to classify the timeout as `upstream_request_timeout`.

#### Scenario: Direct HTTP stream body deadline tie is classified as idle
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** the upstream HTTP response headers have been received
- **WHEN** reading the response body times out just after that shared deadline
- **THEN** the downstream failure event uses `error.code = "stream_idle_timeout"`
- **AND** the error message is `"Upstream stream idle timeout"`

#### Scenario: Pre-response total timeout remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **WHEN** the generic request total timeout fires before an upstream response has started
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Direct HTTP total timeout after recent activity remains request-timeout classified
- **GIVEN** `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`
- **AND** an upstream HTTP response body chunk was received less than a full idle window ago
- **WHEN** the generic request total timeout fires at the request-budget deadline
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Shorter request budget remains request-timeout classified
- **GIVEN** `proxy_request_budget_seconds` is strictly shorter than `stream_idle_timeout_seconds`
- **WHEN** the request budget elapses before the idle timeout
- **THEN** the downstream failure event uses `error.code = "upstream_request_timeout"`
- **AND** the error message is `"Proxy request budget exhausted"`

#### Scenario: Owner-forward receive deadline tie is classified as idle
- **GIVEN** an HTTP bridge owner-forward stream has equal idle and request-budget deadlines
- **AND** the remaining request budget for the next read is at least a full idle window
- **WHEN** receiving the next upstream chunk times out at that shared deadline
- **THEN** the owner-forward timeout uses `error_code = "stream_idle_timeout"`

#### Scenario: Owner-forward shorter remaining budget is request-timeout classified
- **GIVEN** an HTTP bridge owner-forward stream has equal configured idle and request-budget deadlines
- **AND** the remaining request budget for the next read is shorter than a fresh idle window
- **WHEN** receiving the next upstream chunk times out at the request-budget deadline
- **THEN** the owner-forward timeout uses `error_code = "upstream_request_timeout"`

### Requirement: Multiplexed websocket timeout ties preserve younger pending requests
When an upstream websocket or HTTP bridge session has multiple pending Responses turns and the oldest pending turn reaches an equal idle/request-budget deadline, the system MUST NOT fail all pending turns solely because the equal deadline is classified as `stream_idle_timeout`. It MUST fail only pending turns whose own request budget has elapsed, and it MUST keep younger pending turns queued until their own terminal event or timeout.

#### Scenario: Equal deadline on oldest pending request does not fail younger sibling
- **GIVEN** two pending websocket Responses requests share an upstream session
- **AND** the oldest request has reached an equal idle/request-budget deadline
- **AND** the younger request still has request budget remaining
- **WHEN** the upstream receive watchdog fires
- **THEN** the timeout classification is `stream_idle_timeout`
- **AND** the fail-all-pending path is not used
- **AND** only the expired oldest request is failed
- **AND** the younger request remains pending

### Requirement: HTTP bridge streams emit downstream liveness frames while pending
When an HTTP bridge Responses request is waiting for upstream queue events, the system MUST emit a downstream SSE liveness frame at the configured `sse_keepalive_interval_seconds` interval so downstream clients do not disconnect before the upstream terminal frame arrives. The first generated liveness frame MUST be delayed until after the HTTP bridge startup-error probe window so a local startup `ProxyResponseError` can still be surfaced as a non-2xx HTTP response. Once a generated liveness frame is emitted, the stream MUST be considered started for later HTTP-error propagation decisions, so a subsequent upstream `response.failed` is forwarded in-stream instead of being raised as a startup HTTP error. If the pending request already has a response id, the liveness frame MAY be a `response.in_progress` SSE event for that response id. If no response id is known yet, the Codex CLI route MUST emit an ignored `codex.keepalive` SSE data event because comment-only frames do not reset the CLI's EventSource idle timer. Public `/v1/responses` stream normalization MUST preserve SSE comment keepalives instead of treating them as malformed data, and MUST drop `codex.*` liveness events from the public OpenAI SDK contract surface.

#### Scenario: HTTP bridge emits response in-progress keepalive after response id is known
- **GIVEN** an HTTP bridge request has a known response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `response.in_progress` event for that response id
- **AND** the request remains pending

#### Scenario: HTTP bridge emits Codex keepalive before response id is known
- **GIVEN** an HTTP bridge request does not yet have a response id
- **WHEN** no upstream event arrives before the SSE keepalive interval elapses
- **THEN** the downstream stream emits a `codex.keepalive` SSE data event
- **AND** the request remains pending

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

### Requirement: Codex WebSocket pre-created turns receive application heartbeats
When serving the Codex-native `/backend-api/codex/responses` WebSocket route, the proxy SHALL emit a parseable Codex vendor heartbeat while a `response.create` request is pending but upstream has not yet emitted `response.created`. The heartbeat MUST be an application text frame so Codex clients reset stream-idle watchdogs that do not observe WebSocket protocol ping/pong frames. Once upstream assigns a response id, the proxy MUST continue using the existing `response.in_progress` heartbeat shape for that response id.

#### Scenario: Codex websocket upstream is silent before response.created
- **GIVEN** a Codex-native WebSocket `/backend-api/codex/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy emits a `codex.keepalive` text event downstream
- **AND** the request remains pending for the upstream `response.created` or terminal event

#### Scenario: OpenAI-style v1 websocket does not receive Codex vendor heartbeat
- **GIVEN** an OpenAI-style WebSocket `/v1/responses` request is pending
- **AND** upstream has not emitted `response.created` for the request
- **WHEN** no upstream application frame arrives before the configured keepalive interval
- **THEN** the proxy MUST NOT emit a `codex.keepalive` vendor event downstream

### Requirement: WebSocket terminal auth failures recover before visible output

When a Codex or OpenAI-compatible Responses WebSocket request receives an upstream terminal `response.failed` or `error` before downstream-visible output with `error.code = "invalid_api_key"` or `error.type = "authentication_error"`, the proxy MUST treat the failure as account-local auth state instead of immediately surfacing the terminal event. The proxy MUST preserve the existing no-replay rule after downstream-visible output or for non-replayable continuation requests.

#### Scenario: Session-ended WebSocket auth failure uses another account

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal auth failure whose message says the session ended or asks the user to log in again
- **WHEN** another eligible account can complete the request
- **THEN** the downstream WebSocket response succeeds from the other account
- **AND** the selected account is marked re-authentication-required and excluded from that replay

#### Scenario: Generic WebSocket auth failure refreshes once before failover

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal `invalid_api_key` failure
- **WHEN** the forced-refresh replay on the selected account also returns a pre-visible terminal `invalid_api_key` failure
- **THEN** the proxy excludes the selected account and tries another eligible account
- **AND** the downstream WebSocket response succeeds from the other account when it completes

#### Scenario: WebSocket auth failure after visible output is not replayed

- **GIVEN** a WebSocket response has emitted downstream-visible output
- **WHEN** upstream later returns a terminal `invalid_api_key` or `authentication_error`
- **THEN** the proxy MUST surface the terminal error without replaying the request on another account

### Requirement: Compact auth failures fail over after forced refresh

The proxy MUST recover from account-local compact authentication failures before
surfacing them to the compact client. When a `/backend-api/codex/responses/compact`
request receives an upstream `401 invalid_api_key` or `401 token_invalidated`
response for the selected account, the proxy MUST attempt one forced token
refresh and retry the compact request on that same account. If the refreshed
retry also returns `401`, the proxy MUST classify and record the account
failure, exclude that account from the current compact request, and try another
eligible account when one is available. The proxy MUST NOT surface the repeated
account-local `401` to the compact client before exhausting eligible accounts.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Refreshed compact token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 token_invalidated` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is marked `reauth_required`
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry

### Requirement: Pre-visible proxy auth failures fail over after forced refresh

The proxy MUST treat repeated account-local authentication failures as
per-request account failures before any downstream-visible output is emitted.
When a proxy request on a non-compact surface retries with a refreshed token and
the refreshed retry still returns upstream `401 invalid_api_key` or
`401 token_invalidated`, the proxy MUST classify and record the selected account
failure, exclude that account from the current request, and try another eligible
account when one is available. The proxy MUST preserve the existing no-replay
rule after downstream-visible stream or websocket output has been emitted.

#### Scenario: Pre-visible streaming auth failure uses another account

- **GIVEN** at least two accounts are eligible for a streaming responses request
- **AND** the selected account returns `401 invalid_api_key` before downstream-visible output
- **WHEN** another eligible account can complete the request
- **THEN** the downstream stream succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Pre-visible token invalidation uses another account

- **GIVEN** at least two accounts are eligible for a pre-visible proxy request
- **AND** the selected account returns `401 token_invalidated` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is marked `reauth_required`

#### Scenario: Non-stream proxy auth failure uses another account

- **GIVEN** at least two accounts are eligible for a thread-goal, Codex control,
  transcription, or file create/finalize request
- **AND** the selected account returns `401 invalid_api_key` before and after a forced refresh
- **WHEN** another eligible account can complete the request
- **THEN** the downstream request succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

#### Scenario: Websocket connect auth failure uses another account

- **GIVEN** at least two accounts are eligible for an upstream websocket connect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket
- **THEN** the websocket connect path excludes the invalidated account and tries another account

#### Scenario: HTTP bridge handshake auth failure uses another account

- **GIVEN** at least two accounts are eligible for HTTP bridge session creation or reconnect
- **AND** the selected account returns `401 invalid_api_key` after a forced refresh retry
- **WHEN** another eligible account can open the upstream websocket handshake
- **THEN** the HTTP bridge path excludes the invalidated account and tries another account

### Requirement: Codex WebSocket wrapped errors follow official client shape

When serving `/backend-api/codex/responses` or bridge-backed Responses WebSocket traffic, the service MUST classify upstream `type: "error"` frames using the same wrapped-error shape that the official Codex client accepts: a non-2xx `status` or `status_code` field indicates an upstream HTTP-style error, and the error detail MAY appear either in a nested `error` object or in top-level fields such as `code`, `message`, `param`, and `error_type`.

Top-level error normalization MUST NOT treat the event discriminator `type: "error"` as the upstream error type. If the frame provides `error_type`, the service MUST use that value as the error type for classification/rewrites. Existing continuity protection remains authoritative: frames describing `previous_response_not_found` MUST be rewritten or recovered through the established `stream_incomplete` continuity path instead of exposing the raw upstream code or missing response id.

#### Scenario: status_code alias is classified as upstream error status

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status_code":400,...}`
- **THEN** the service treats the HTTP-style error status as `400`
- **AND** applies the same error classification path as for `status: 400`

#### Scenario: top-level error_type is used for classification

- **WHEN** an upstream Codex WebSocket frame is `{"type":"error","status":400,"error_type":"invalid_request_error","code":"previous_response_not_found",...}`
- **THEN** the normalized error detail has `type = "invalid_request_error"`
- **AND** the event discriminator `type = "error"` is not used as the upstream error type

#### Scenario: top-level previous-response miss remains masked

- **WHEN** a `/backend-api/codex/responses` WebSocket follow-up has `previous_response_id`
- **AND** upstream emits a top-level `previous_response_not_found` wrapped-error frame using `status_code`
- **THEN** the downstream event is a retryable continuity failure such as `stream_incomplete`
- **AND** the downstream payload does not contain `previous_response_not_found`
- **AND** the downstream payload does not expose the missing previous response id

### Requirement: Backend Codex Responses preserve advertised image_generation tools

The service MUST accept HTTP and websocket `/backend-api/codex/responses`
request-create payloads that include top-level `tools` entries with
`type: "image_generation"`. During shared Responses validation and upstream
forwarding, the service MUST preserve those top-level `image_generation` tool
entries so Codex clients can expose and use the built-in image-generation
surface. The service MUST also preserve all other tool entries and the existing
built-in tool forwarding policy for public `/v1/*` routes.

#### Scenario: Backend Codex HTTP request preserves advertised image_generation tool

- **WHEN** a client sends `POST /backend-api/codex/responses` with
  `tools=[{"type":"image_generation"},{"type":"function","name":"x"}]`
- **THEN** the request is accepted instead of failing with
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool
- **AND** the remaining `function` tool is preserved

#### Scenario: Backend Codex websocket create preserves advertised image_generation tool

- **WHEN** a websocket `response.create` payload for
  `/backend-api/codex/responses` includes a top-level
  `{"type":"image_generation"}` tool entry
- **THEN** the backend Codex websocket request is accepted
- **AND** the forwarded upstream `response.create` payload preserves that
  `image_generation` tool entry

#### Scenario: Public v1 Responses built-in forwarding policy remains unchanged

- **WHEN** a client sends `/v1/responses` with
  `tools=[{"type":"image_generation"}]`
- **THEN** the service does not locally reject the built-in tool as an
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool

### Requirement: HTTP bridge startup waits fail with terminal local overload

When the HTTP responses bridge cannot start upstream work because its local bridge startup waits do not make progress within the configured proxy admission wait timeout, the service MUST surface a terminal local-overload error instead of leaving `/v1/responses`, `/backend-api/codex/responses`, or compact responses streams on keepalives only.

#### Scenario: HTTP bridge startup wait stalls before first upstream event

- **WHEN** a streaming Responses request enters the HTTP responses bridge
- **AND** bridge startup is blocked by local bridge admission state before any upstream `response.*` event can be emitted
- **AND** the wait exceeds the configured proxy admission wait timeout
- **THEN** the request fails with a terminal error
- **AND** the error payload identifies local proxy overload with `error.code = "proxy_overloaded"`

### Requirement: Accept duplicated /v1/ prefix under /backend-api/codex
The service MUST treat any inbound request whose path begins with `/backend-api/codex/v1/` followed by a non-empty rest as a transparent alias for the same path with the `/v1` segment removed. Some OpenAI-compatible clients append `/v1/` to whatever the operator configured as the base URL, producing paths like `/backend-api/codex/v1/models` or `/backend-api/codex/v1/responses`. The aliasing MUST be applied before routing so the canonical handler runs unchanged. The aliasing MUST NOT trigger for `/backend-api/codex/v1` or `/backend-api/codex` with no further path. The top-level OpenAI-style `/v1/<rest>` routes are unaffected.

#### Scenario: Misbehaving client requests duplicated prefix
- **WHEN** a client requests `GET /backend-api/codex/v1/models`
- **THEN** the response is identical to `GET /backend-api/codex/models`

#### Scenario: Canonical paths are unchanged
- **WHEN** a client requests `GET /backend-api/codex/models` or `GET /v1/models`
- **THEN** the request is routed to its existing handler without modification

### Requirement: Backend Responses endpoint accepts OpenAI-compatible request shapes
The `/backend-api/codex/responses` HTTP endpoint SHALL accept the OpenAI-compatible Responses request shape used by `/v1/responses`, including a plain string `input` and omitted or explicit `null` `instructions`. The endpoint MUST normalize that request into the internal Responses request model before forwarding upstream, MUST continue returning `text/event-stream` SSE Responses events, and MUST preserve Codex-specific session/cache affinity behavior for the backend route.

#### Scenario: OpenAI SDK streams through backend Responses path
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with `stream=true`, a model, and a plain string `input`
- **THEN** the proxy accepts the request without requiring `instructions`
- **AND** the response is a `text/event-stream` stream containing Responses events such as `response.output_text.delta` and `response.completed`

#### Scenario: Codex-private stream metadata is hidden from OpenAI SDK clients
- **WHEN** upstream emits a Codex-private stream event such as `codex.rate_limits` before `response.created`
- **THEN** the HTTP Responses stream omits the private event from the downstream SSE body
- **AND** OpenAI SDK clients can consume the stream without failing their Responses event ordering checks

#### Scenario: Strict function tool schemas are validated before streaming
- **WHEN** an OpenAI-compatible client sends `POST /backend-api/codex/responses` with a strict function tool schema that violates the supported JSON Schema subset
- **THEN** the proxy rejects the request with a deterministic 400 `invalid_function_parameters` error before opening the stream

#### Scenario: Codex-native backend Responses shape is preserved
- **WHEN** a Codex client sends `POST /backend-api/codex/responses` with `instructions`, array-shaped `input`, and Codex affinity headers
- **THEN** the proxy preserves the normalized request content and continues applying backend Codex session affinity

### Requirement: Codex WebSocket stale-anchor failures remain recoverable by a full-context retry
When serving or consuming the Codex-native `/backend-api/codex/responses` WebSocket route, upstream `previous_response_id` MUST be treated as an ephemeral optimization rather than durable conversation state. A stale-anchor continuity failure during a long-wait tool-output continuation MUST NOT hard-end the user turn before one full-context retry without `previous_response_id` has been attempted.

#### Scenario: Long-running terminal wait invalidates the upstream previous response anchor
- **GIVEN** a Codex-native WebSocket session has completed a response with id `resp_old`
- **AND** the client later sends a `response.create` frame with `previous_response_id: "resp_old"` and tool-output or other delta input after a long idle period
- **WHEN** the upstream rejects `resp_old` with a stale-anchor error such as `previous_response_not_found`
- **THEN** the failure is classified as stale-anchor continuity loss
- **AND** the client-side recovery path retries once using full conversation history without `previous_response_id` before surfacing a turn-ending error
- **AND** the downstream/user-visible error path does not expose raw `previous_response_not_found` or the missing upstream response id

#### Scenario: codex-lb sanitizes stale-anchor errors for client classification
- **WHEN** upstream emits a direct WebSocket stale-anchor error
- **THEN** codex-lb MUST NOT forward raw `previous_response_not_found`
- **AND** codex-lb MUST NOT expose the missing upstream response id downstream
- **AND** codex-lb MUST preserve a stable sanitized classifier that lets a compatible Codex client distinguish stale-anchor continuity loss from quota, policy, auth, and generic invalid-request failures

#### Scenario: Non-stale-anchor failures do not trigger full-context retry
- **WHEN** the upstream failure is quota, policy, auth, context-window, or another non-continuity error
- **THEN** the client MUST NOT convert it into a stale-anchor full-context retry
- **AND** codex-lb MUST preserve the original error class as much as safely possible

### Requirement: Codex WebSocket continuity source of truth is centralized
The behavior for Codex-native WebSocket previous-response continuity MUST be specified in this OpenSpec change rather than route-local or branch-local ad hoc patches. Future changes to this behavior MUST update the OpenSpec requirements before modifying code.

#### Scenario: Previous-response fix changes behavior
- **WHEN** a patch changes routing, replay, masking, retry, or failure behavior for Codex-native WebSocket `previous_response_id`
- **THEN** the patch includes an OpenSpec delta or updates the active continuity source of truth
- **AND** direct `/backend-api/codex/responses` WebSocket tests or Codex client WebSocket tests cover the changed behavior

### Requirement: Direct WebSocket previous-response misses never leak raw upstream errors
When a direct Responses WebSocket request depends on `previous_response_id`, the service MUST NOT send a raw upstream `previous_response_not_found` payload to the downstream client. This applies to `/v1/responses` and `/backend-api/codex/responses` WebSocket clients.

#### Scenario: Codex Desktop continue receives upstream previous-response miss before response.created
- **WHEN** a direct WebSocket `response.create` request includes `previous_response_id`
- **AND** upstream emits a top-level `type=error` payload with `code=previous_response_not_found` or `param=previous_response_id`
- **AND** no stable upstream `response.id` has been assigned yet
- **THEN** the downstream client receives either a transparent replay result or a retryable terminal event
- **AND** the downstream payload does not include `previous_response_not_found`
- **AND** the downstream payload does not include the missing previous response id

#### Scenario: Codex Desktop continue has only request-log owner metadata
- **WHEN** a prior direct WebSocket turn completed and was persisted only in `request_logs`
- **AND** a later direct WebSocket follow-up references that completed response id
- **THEN** owner lookup uses request-log metadata or fails closed with a retryable error
- **AND** it does not continue on an unpinned account
- **AND** it does not expose raw `previous_response_not_found`

### Requirement: Failed precreated HTTP bridge replay retires stale sessions

When an HTTP bridge request is still pending before upstream `response.completed` and the upstream websocket closes or times out before the pending request can be completed, the service MUST fail the pending request terminally and retire the affected bridge session if precreated replay does not reconnect and resend successfully.

#### Scenario: Precreated replay fails after upstream disconnect

- **WHEN** an HTTP bridge request is pending before `response.completed`
- **AND** the upstream websocket closes before the request completes
- **AND** precreated replay fails to reconnect and resend the request
- **THEN** the pending request is removed from the bridge queue
- **AND** the per-session response-create gate is released
- **AND** the bridge session is closed and removed from local reuse
- **AND** the terminal error preserves the original failure code such as `stream_incomplete` or `upstream_request_timeout`

#### Scenario: Terminal logging failure does not preserve stale bridge ownership

- **WHEN** a failed pending HTTP bridge request is being logged as terminal
- **AND** request-log writing fails
- **THEN** the service still removes the stale bridge session from local reuse
- **AND** the service releases any durable bridge ownership for that stale session

#### Scenario: Concurrent waiter cannot submit on retired stale bridge

- **WHEN** an HTTP bridge request is waiting on a session response-create gate
- **AND** the upstream reader retires that same bridge session after a failed precreated replay
- **THEN** the waiting request or prewarm is rejected before it is appended to pending requests or sent upstream
- **AND** the retired bridge session remains closed and removed from local reuse
- **AND** the post-admission ownership check, pending enqueue, and upstream send are mutually exclusive with stale-session retirement

#### Scenario: Unregistered stale bridge reference cannot submit after admission

- **WHEN** an HTTP bridge request or prewarm holds a stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected after response-create gate admission and before it is appended or sent upstream
- **AND** response-create gate and admission state acquired by the rejected request is released

#### Scenario: Unregistered closed bridge reference cannot reconnect

- **WHEN** an HTTP bridge request holds a closed stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected before attempting to reconnect the stale bridge upstream

#### Scenario: Reader crash closes bridge before releasing pending gate

- **WHEN** an HTTP bridge upstream reader crashes while a pending request owns the response-create gate
- **AND** another request or prewarm is waiting on that same gate
- **THEN** the crashed bridge session is marked closed before the pending request gate is released
- **AND** the waiting request or prewarm cannot submit on the crashed bridge
- **AND** the crashed bridge session is removed from local reuse and its upstream resources are closed

#### Scenario: Prewarm cleanup does not consume visible queue slots

- **WHEN** a prewarm request is rejected or interrupted after response-create gate admission
- **AND** a visible HTTP bridge request is still counted in the session queue
- **THEN** prewarm cleanup releases its response-create gate and admission state
- **AND** the visible request queue count is preserved

### Requirement: Pre-dispatch Responses requests recover from local network transitions

When a Responses request encounters a classified local DNS or host-route failure and the transport proves that request dispatch did not occur, the proxy MUST retry on the same account with bounded backoff until the attempt succeeds or the existing request budget expires. A classified token-refresh network failure MUST receive the same bounded same-account recovery only when typed transport provenance proves the refresh POST was not dispatched. Recovery MUST NOT move account-owned continuation or file state to another account. Recovery client rotation, client construction, cleanup, and sleep MUST remain inside the original monotonic deadline, and existing keepalive behavior MUST remain active while an HTTP/SSE client waits. Post-connect send or receive failures, response/body-read failures, and serialized terminal response events with uncertain upstream delivery MUST retain the account-neutral network classification but MUST NOT be transparently replayed.

#### Scenario: HTTP stream survives a temporary DNS outage

- **WHEN** a streaming Responses request fails DNS resolution before request dispatch
- **AND** DNS resolution recovers before the request budget expires
- **THEN** the proxy retries the request on the same account
- **AND** the downstream stream receives the recovered upstream response instead of a terminal network error

#### Scenario: Native WebSocket connect survives a temporary DNS outage

- **WHEN** a native Responses WebSocket request cannot open its upstream WebSocket because of a classified local network failure
- **AND** connectivity recovers before the request budget expires
- **THEN** the proxy opens the upstream WebSocket on the same account
- **AND** does not exhaust or exclude unrelated accounts

#### Scenario: Recovery remains bounded

- **WHEN** the local network does not recover before the configured request budget expires
- **THEN** the proxy terminates the request with `error.code = "upstream_request_timeout"` and message `"Proxy request budget exhausted"`
- **AND** does not extend the deadline or replay downstream-visible output

#### Scenario: Token refresh survives a temporary DNS outage

- **WHEN** token refresh for the selected account reports a classified process-network failure
- **AND** typed transport provenance proves the refresh POST was not dispatched
- **AND** connectivity recovers within the original request deadline
- **THEN** the proxy retries refresh on the same account
- **AND** does not record the network failure against the account

#### Scenario: Token refresh response failure is not replayed

- **WHEN** token refresh reports a classified process-network failure while reading the response or body
- **AND** the proxy cannot prove the refresh POST was not dispatched
- **THEN** the failure retains the account-neutral process-network code
- **AND** the proxy does not retry the possibly consumed rotating refresh token

#### Scenario: Ambiguous compact POST failure is not replayed

- **WHEN** a compact POST reports a classified process-network failure without typed pre-dispatch provenance
- **THEN** the compact failure retains the account-neutral process-network code
- **AND** the proxy does not replay, penalize, or exclude the selected account

#### Scenario: Serialized terminal network failure is not replayed

- **WHEN** an upstream stream emits a terminal response event carrying the process-network code
- **AND** the proxy cannot prove that request dispatch did not occur
- **THEN** the terminal event is surfaced without transparent replay
- **AND** the selected account's health remains unchanged

#### Scenario: Post-connect WebSocket network failure is not replayed speculatively

- **WHEN** an upstream WebSocket send or receive reports a classified process-network failure after the connection opened
- **AND** the proxy cannot prove that `response.create` was not delivered
- **THEN** the pending request fails with the account-neutral process-network code
- **AND** the proxy does not transparently replay the request

### Requirement: File-pinned compact refresh/connect failures fail closed

The proxy SHALL preserve file-owner routing during pre-visible refresh and
upstream-connect failure handling. If the pinned account cannot refresh or open
the upstream compact connection before any compact response is emitted, the proxy
MUST surface a stable upstream-unavailable failure for that request instead of
excluding the pinned account and replaying the compact request on another
account. This fail-closed rule applies only to file-pinned compact requests;
replayable compact/connect requests without a live file-id pin continue to use
the existing pre-visible forced-refresh and eligible-account failover behavior.

#### Scenario: file-pinned compact request fails closed on refresh transport failure

- **GIVEN** `file_pinned` was uploaded through `account_a` and its in-memory pin is live
- **AND** a compact request references `{"type": "input_file", "file_id": "file_pinned"}`
- **WHEN** `account_a` fails token refresh with a pre-visible transport or connection error
- **THEN** the proxy returns an upstream-unavailable error for that compact request
- **AND** it does not select another account for that request

#### Scenario: replayable compact request without file pins can still fail over

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the compact request has no live `input_file.file_id` routing pin
- **WHEN** the selected account fails before compact output is emitted and the
  failure is classified by an existing pre-visible failover rule
- **THEN** the proxy may exclude that account for the current request and try
  another eligible account

#### Scenario: retained file-backed bridge replay remains owner-bound

- **GIVEN** an HTTP bridge precreated request uses a proxy-injected
  `previous_response_id` anchor
- **AND** the retained retry-safe full body references an account-scoped
  uploaded file through `input_file.file_id` or file-backed `input_image`
- **WHEN** the bridge retries after an upstream close before visible output
- **THEN** the proxy keeps the anchored request owner-bound instead of stripping
  the anchor, excluding the owner, and replaying the file reference on another
  account
- **AND** if the file owner cannot be reselected, the retry fails closed instead
  of reconnecting the bridge on a replacement account

#### Scenario: verified owner refresh failover releases the failed stream lease

- **GIVEN** a streaming request selects the previous-response owner and holds an
  account stream lease
- **AND** a locally verified full resend permits failover after that owner fails
  refresh or connect before output is emitted
- **WHEN** the proxy excludes the failed owner and selects a replacement account
- **THEN** the failed owner's stream lease is released before replacement
  selection so the owner does not retain stale local pressure

### Requirement: Stale HTTP bridge previous-response aliases fail closed

The HTTP bridge MUST NOT treat a stale previous-response alias as a model
transition unless the indexed session's model is incompatible with the incoming
request. When a previous-response alias resolves to a closed or inactive session
for the same model and no durable recovery owner is available, the proxy MUST
surface the existing continuity-lost failure instead of creating or selecting a
replacement bridge.

#### Scenario: stale same-model previous-response alias fails closed

- **GIVEN** the previous-response index still points to an inactive HTTP bridge
  session for the same model
- **AND** no durable owner lookup is available for that response id
- **WHEN** a request arrives with that `previous_response_id`
- **THEN** the proxy fails closed with the stream-incomplete continuity error
- **AND** it does not create a replacement bridge for the stale response id

### Requirement: Cross-account bridge retries clear turn-state

When a pre-visible HTTP bridge request is proven safe to replay on another account, the proxy MUST clear the retired account's upstream and downstream turn-state before opening the replacement connection. The replacement handshake MUST NOT carry an `x-codex-turn-state` header learned from the excluded account.

#### Scenario: safe bridge replay excludes the stalled account

- **GIVEN** a pre-visible HTTP bridge request is proven safe to replay
- **WHEN** the failed bridge account is excluded before reconnect
- **THEN** the proxy clears the retired account's turn-state fields and header
- **AND** the replacement account receives no turn-state from the retired socket

### Requirement: Pre-visible unary refresh/connect failures fail over

For unary proxy requests that have not emitted downstream-visible output, the proxy MUST treat retryable token-refresh or upstream-connect transport failures as account-local transient failures.

This applies to Codex thread-goal requests, Codex control requests,
transcription requests, and file create/finalize requests. When another
eligible account is available within the request budget, the proxy MUST record
the failed account, exclude it from the current request, and retry the unary
operation on the fallback account. The proxy MUST NOT fail over strict
account-owner requests whose upstream resource is bound to the selected account.

#### Scenario: Unary refresh transport failure uses another account

- **GIVEN** at least two accounts are eligible for a Codex thread-goal, Codex
  control, transcription, or file-create request
- **AND** the selected account fails during token refresh or upstream connect
  with a retryable transient transport error before downstream-visible output
- **WHEN** another eligible account can complete the request within the request
  budget
- **THEN** the downstream request succeeds from the fallback account
- **AND** the failed account is recorded and excluded from further attempts for
  that request

#### Scenario: Strict file-owner refresh failure fails closed

- **GIVEN** a file-finalize request is pinned to the account that owns the file
- **AND** the pinned account fails during token refresh or upstream connect with
  a retryable transient transport error before downstream-visible output
- **WHEN** another account would otherwise be eligible for proxy traffic
- **THEN** the proxy fails the request with an upstream-unavailable error
- **AND** the proxy does not send the file-finalize operation through another
  account

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw HTTP
Responses stream path. This bypass MUST happen after rejecting unsupported
uploaded-image references and MUST be limited to the current request; subsequent
text-only requests MAY continue using the HTTP responses bridge.

The raw HTTP path is the source of truth for image validation and upstream image
error semantics. The bridge MUST NOT hold image requests waiting for
`response.created` when upstream rejects an invalid inline image payload.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **THEN** the request is sent through the raw HTTP stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, codex-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** codex-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** codex-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** codex-lb emits a non-terminal `codex_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** codex-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

#### Scenario: Pinned requests are not moved to another account

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **THEN** codex-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure.

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** codex-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. A permitted
file-free retry MUST select the replacement with cleared request and session
affinity, but MUST validate any raw legacy owner before changing the live
session or its durable owner generation. On success it MUST make exactly one
durable replacement claim before swapping the session, then clear or replace
the session affinity and local turn-state aliases. A legacy-owner conflict MUST
leave the original session open and unchanged. File-pinned requests MUST NOT
migrate.

#### Scenario: Created HTTP bridge response is not replayed

- **WHEN** an HTTP bridge request has emitted `response.created` before a
  security-work authorization denial
- **THEN** the service does not reconnect or resend the request on another
  account
- **AND** it forwards the original terminal error

#### Scenario: Deferred reasoning blocks replay

- **WHEN** an HTTP bridge request buffers a reasoning prelude before a
  security-work authorization denial
- **THEN** that prelude blocks account-switch replay and is not emitted before
  the terminal security decision

#### Scenario: Legacy owner conflict fails before replacement mutation

- **GIVEN** a session-header security retry selects an authorized replacement account
- **AND** the raw legacy affinity row belongs to a different account
- **WHEN** the service validates the replacement
- **THEN** it does not claim the durable session for the replacement
- **AND** it leaves the original account, upstream, owner generation, aliases, and open session unchanged

### Requirement: Responses request compatibility controls

The system SHALL accept OpenAI-compatible Responses request controls that clients may send for `/v1/responses` and `/backend-api/codex/responses` when those controls can be safely normalized before the ChatGPT-backed upstream request. Specifically, `truncation` values `"auto"` and `"disabled"` MUST pass request validation and MUST be omitted from the upstream payload because the current ChatGPT-backed path does not consume the field. Unsupported `truncation` values MUST still be rejected with HTTP 400.

#### Scenario: Truncation auto is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "auto"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

#### Scenario: Truncation disabled is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "disabled"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

### Requirement: HTTP bridge stale-session cleanup is bounded

The HTTP responses bridge MUST NOT hold the global bridge session registry lock
while awaiting operations that can block on a stale session's upstream websocket,
per-session pending lock, durable session repository, account lease release, or
other external cleanup work.

When stale bridge sessions are discovered during `/v1/responses`,
`/backend-api/codex/responses`, `/v1/responses/compact`, or
`/backend-api/codex/responses/compact` startup, the registry lock MAY be used to
remove closed or idle sessions from in-memory indexes, but potentially blocking
session close/fail-pending work MUST run after the lock is released or under a
bounded cleanup path. A wedged stale session MUST NOT prevent unrelated soft
HTTP Responses work from creating or reusing another bridge session.

Idle pruning MUST make pending-request decisions only while holding the
session's pending-request lock. If that lock cannot be acquired immediately,
the service MUST skip pruning that session instead of inferring that it is idle
from unlocked pending-request state.

If cleanup cannot complete within the bounded cleanup path, the service MUST log
a low-cardinality local bridge cleanup warning and continue protecting registry
progress. Requests that cannot safely proceed because a hard-continuity session
is unavailable MUST fail closed with an explicit local overload or continuity
error rather than silently hanging.

When a replacement bridge session claims the same durable key after stale local
session detachment, the durable owner generation MUST advance so that a late
cleanup from the stale local session cannot release or close the replacement
session's durable ownership. This MUST also apply when the detached local
session is retiring but still has visible in-flight requests and will release
its durable ownership later after draining. After a detached retiring session
finishes draining its visible requests, it MUST release its durable ownership
and account lease instead of only closing the upstream websocket.
If that retirement is initiated by the upstream-reader task after processing
the terminal upstream event, session close MUST NOT cancel or await the current
upstream-reader task itself.

When bridge capacity eviction removes an idle local session to admit a
replacement session, the evicted session's close MUST be awaited through a
bounded path before the replacement selects an account, so the evicted
session's account lease cannot cause a spurious no-account or local-capacity
failure.

If a request is cancelled while awaiting that pre-creation eviction close after
registering replacement session creation as in-flight, the service MUST fail or
remove the in-flight creation marker before propagating cancellation. Later
requests MUST NOT wait on an orphaned creation future that can never complete.

#### Scenario: wedged stale pending lock does not block fresh soft request

- **GIVEN** the HTTP responses bridge has an idle or stale local session whose
  pending-request lock does not complete promptly
- **WHEN** a new soft-affinity `/v1/responses` request starts bridge session
  selection
- **THEN** the global bridge registry lock is not held indefinitely by stale
  cleanup
- **AND** the stale session is not pruned based on unlocked pending-request
  state
- **AND** the new request either creates/reuses an eligible bridge session or
  returns an explicit bounded local error
- **AND** it does not hang before account selection or bridge create/reuse
  logging

#### Scenario: stale close runs outside registry lock

- **GIVEN** bridge startup identifies an idle stale session that must be closed
- **WHEN** closing that session awaits upstream-reader cancellation, websocket
  close, durable release, or account lease release
- **THEN** the global bridge registry lock is already released
- **AND** unrelated bridge startup requests can continue to inspect or mutate
  the registry

#### Scenario: stale durable release cannot fence out replacement owner

- **GIVEN** a stale or retiring bridge session for a durable key is replaced by
  a new local session after local detachment
- **WHEN** the stale session's bounded background close releases durable
  ownership after the replacement has claimed the same durable key
- **THEN** the stale release does not clear the replacement owner's durable
  lease
- **AND** follow-up requests for the replacement session do not receive a
  spurious bridge owner mismatch caused by the stale close

#### Scenario: detached retiring session releases resources after drain

- **GIVEN** a retiring bridge session was detached while visible requests were
  still draining
- **WHEN** those visible requests drain and the session is retired
- **THEN** the service releases the old session's durable ownership
- **AND** the service releases the old session's account lease
- **AND** upstream-reader-owned retirement does not self-cancel the current
  upstream reader task
- **AND** the detached session no longer holds bridge capacity until process
  exit

#### Scenario: LRU eviction releases lease before replacement account selection

- **GIVEN** the bridge is at local session capacity and an idle session is
  selected for LRU eviction
- **WHEN** a replacement bridge session is created after that eviction
- **THEN** the evicted session is closed through a bounded path before the
  replacement selects an account
- **AND** the evicted session's account lease does not cause the replacement to
  fail with a spurious no-account or local-capacity error

#### Scenario: cancellation during LRU close clears in-flight creation

- **GIVEN** the bridge is at local session capacity and an idle session is
  detached for LRU eviction before replacement creation
- **WHEN** the replacement request is cancelled while the bounded eviction close
  is still awaiting cleanup
- **THEN** the replacement in-flight creation marker is removed or failed before
  cancellation is propagated
- **AND** later requests for the same bridge key do not wait on that abandoned
  creation marker

### Requirement: Codex compaction triggers are bridged into compact output

When `POST /backend-api/codex/responses` receives a request whose top-level `input` array contains exactly one `{"type":"compaction_trigger"}` item as its final element, the proxy SHALL remove that trigger before calling upstream compaction handling and SHALL emit a raw SSE stream that contains exactly one compaction output item.

The stream MUST include a `response.output_item.done` event whose `item` is a `compaction` record, and the terminal `response.completed` event MUST carry the same single compaction item in `response.output`. When the selected encrypted upstream compaction item carries a non-empty `id`, both events MUST preserve that exact ID with its `encrypted_content` so a later replay retains the ciphertext's item binding.

For Codex-affinity standalone compact requests, `POST /backend-api/codex/responses/compact` SHALL normalize an upstream remote-compaction-v2 response that includes historical message output plus a compaction summary into the single compact output item required by Codex clients. A non-empty upstream compaction item `id` MUST be preserved in that normalized output item.

OpenAI-style `/v1/responses/compact` is unchanged by this requirement.

#### Scenario: terminal trigger is converted into a compact stream
- **WHEN** a `POST /backend-api/codex/responses` request ends with exactly one top-level `compaction_trigger`
- **THEN** the proxy strips the trigger, invokes compact handling, and streams one `response.output_item.done` event containing a `compaction` item
- **AND** the terminal `response.completed` event carries that same item in `response.output`

#### Scenario: encrypted compaction item ID survives trigger streaming
- **WHEN** compaction handling for a terminal trigger returns encrypted content in an item with a non-empty `cmp_*` ID
- **THEN** the `response.output_item.done` item preserves that exact ID
- **AND** the `response.completed` output item preserves the same ID with the same encrypted content

#### Scenario: malformed trigger placement is rejected
- **WHEN** a `POST /backend-api/codex/responses` request contains a duplicated or non-terminal top-level `compaction_trigger` item
- **THEN** the proxy returns HTTP 400 with `invalid_request_error`
- **AND** it does not attempt upstream compaction handling

#### Scenario: Codex-affinity standalone compact normalizes remote v2 output
- **WHEN** a Codex-affinity `POST /backend-api/codex/responses/compact` request receives upstream output that contains historical message items and one compaction summary item
- **THEN** the JSON response body contains exactly one `output` item for that compaction summary
- **AND** the normalized item preserves the compaction summary's non-empty upstream ID
- **AND** it does not expose historical message items as standalone compact output

### Requirement: Request logs expose upstream Responses transport
For streaming Responses proxy requests, persisted request logs MUST distinguish the downstream client transport from the upstream egress transport by recording the upstream transport in `request_logs.upstream_transport` while preserving `request_logs.transport` as the downstream client transport.

#### Scenario: downstream HTTP single-shot records upstream HTTP
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing chooses upstream HTTP for a single-shot Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"http"`

#### Scenario: downstream HTTP sticky records preserved auto upstream mode
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"auto"`

#### Scenario: historical or unrelated rows tolerate missing upstream transport
- **GIVEN** a request log row predates upstream transport persistence or belongs to a request kind that does not know its upstream transport
- **WHEN** the row is read
- **THEN** `upstream_transport` MAY be null
- **AND** the existing request-log response MUST remain valid

### Requirement: Request Logs API returns upstream transport
The Request Logs API MUST include `upstream_transport` on each request log entry so operators and dashboards can query upstream egress transport without overloading the existing downstream `transport` field.

#### Scenario: request logs response includes upstream transport
- **GIVEN** a persisted request log has `transport = "http"` and `upstream_transport = "auto"`
- **WHEN** a dashboard client fetches request logs
- **THEN** the returned entry includes `transport: "http"`
- **AND** the returned entry includes `upstream_transport: "auto"`

### Requirement: Upstream transport decisions emit low-cardinality metrics
Streaming Responses proxy requests MUST emit a low-cardinality Prometheus counter for upstream transport decisions. The metric MUST NOT include request id, account id, API key id, model, prompt cache key, or other high-cardinality identifiers.

#### Scenario: transport decision counter labels are bounded
- **WHEN** a streaming Responses request completes or terminates with an error
- **THEN** `codex_lb_upstream_transport_decisions_total` is incremented once
- **AND** its labels include only `downstream_transport`, `upstream_transport`, `policy`, `sticky`, and `status`
- **AND** `status` is `"success"` or `"error"`

### Requirement: Raw Responses streams require a terminal SSE event for success

For raw HTTP streaming Responses attempts, the proxy MUST NOT record request-log
status `success` or mark the selected account successful unless the stream
observed a terminal SSE event: `response.completed`, `response.failed`,
`response.incomplete`, or `error`. This requirement applies even when the
upstream HTTP response status was 200 because the stream body remains part of
the request outcome.

If the upstream iterator ends before a terminal event, the proxy MUST surface a
terminal `response.failed` SSE event with error code `stream_incomplete`, record
the request-log row as an upstream `stream_incomplete` error, and apply the
normal transient upstream account-health signal. If the downstream client
cancels or disconnects before a terminal event, the proxy MUST record the
request-log row as a downstream `client_disconnected` error and MUST NOT
penalize the upstream account.

#### Scenario: Raw stream upstream EOF is not successful

- **GIVEN** a raw HTTP streaming Responses request has emitted non-terminal SSE
  data
- **WHEN** the upstream stream ends before `response.completed`,
  `response.failed`, `response.incomplete`, or `error`
- **THEN** the downstream stream receives a terminal `response.failed` event
  with error code `stream_incomplete`
- **AND** the request log stores status `error`, error code
  `stream_incomplete`, and upstream failure metadata
- **AND** the selected account receives a transient upstream failure signal

#### Scenario: Raw stream downstream cancellation is client-side

- **GIVEN** a raw HTTP streaming Responses request has not observed a terminal
  SSE event
- **WHEN** the downstream client cancels or disconnects from the stream
- **THEN** the request log stores status `error`, error code
  `client_disconnected`, and downstream failure metadata
- **AND** the selected account is not penalized for the client-side close

### Requirement: Responses SSE parsing uses only CR/LF line boundaries

When parsing streamed Responses Server-Sent Events, the service MUST treat only
CR (`\r`), LF (`\n`), and CRLF (`\r\n`) as SSE line boundaries. The parser MUST
NOT split a `data:` field on other Unicode line-boundary characters such as
U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR when those characters appear
inside the payload value. Multi-line `data:` fields delimited by CR, LF, or CRLF
MUST continue to be joined with `\n` before JSON decoding.

The streaming HTTP receive path MUST also treat CR-only blank lines (`\r\r`) as
complete SSE event separators, and any normalization of legacy event aliases
MUST preserve the event block's original CR, LF, or CRLF terminator style.

#### Scenario: Unicode separators inside JSON strings are preserved

- **WHEN** an upstream Responses SSE event contains a `data:` JSON payload whose
  string value includes unescaped U+2028 or U+2029
- **THEN** the parser preserves those characters inside the JSON string
- **AND** the event remains available to downstream response-event processing

#### Scenario: CR/LF-delimited multi-line data still joins

- **WHEN** an upstream Responses SSE event contains multiple `data:` lines
  delimited by CR, LF, or CRLF
- **THEN** the parser joins the field values with `\n`
- **AND** continues JSON decoding against the joined payload

#### Scenario: CR-only event separators dispatch complete events

- **WHEN** the HTTP streaming receive path receives an upstream SSE event ending
  in a CR-only blank line
- **THEN** it dispatches that event without waiting for EOF or an LF delimiter
- **AND** legacy event alias normalization preserves the CR-only blank-line
  terminator

### Requirement: Timed-out startup probes MUST settle first-item task exceptions

The proxy MUST retrieve eventual first-item task exceptions when a Responses or
chat-completions startup error probe times out while its first-item task is
still running and the returned stream is abandoned before iteration resumes.
This MUST prevent unhandled asyncio task diagnostics such as `Task exception was
never retrieved` or shielded-future exception logs for upstream
`ProxyResponseError` failures that arrive after the probe timeout.

If the returned stream is consumed later, the task result or exception MUST
remain observable through normal stream iteration.

#### Scenario: Abandoned timed-out probe consumes first-item exception

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **AND** the first-item task later raises `ProxyResponseError`
- **WHEN** the request path abandons the returned stream before consuming that task
- **THEN** the event loop does not emit an unhandled task-exception diagnostic
- **AND** task ownership is settled without changing the client-visible result

#### Scenario: Consumed timed-out probe preserves stream behavior

- **GIVEN** a startup probe times out before the first upstream stream item is available
- **WHEN** the caller later iterates the returned stream
- **THEN** the first task's result or exception is still yielded or raised through the returned stream

### Requirement: Codex installation metadata is account-owned

For Codex response-create upstream requests, the service MUST attach a
server-owned per-account Codex installation id to upstream client metadata when
an account is selected. Inbound client-supplied Codex installation id headers or
metadata MUST NOT be trusted as the account installation id. Existing unrelated
client metadata such as turn metadata MUST be preserved.

#### Scenario: Inbound installation id is replaced

- **GIVEN** an account has a stored Codex installation id
- **AND** a client sends response-create metadata with a different
  `x-codex-installation-id`
- **WHEN** the request is forwarded upstream
- **THEN** the upstream metadata contains the account's stored installation id
- **AND** preserves unrelated metadata entries

#### Scenario: Inbound installation id header is stripped

- **WHEN** a client sends `X-Codex-Installation-Id`
- **THEN** the upstream request does not forward that header as a trusted
  client-supplied identity

### Requirement: Compact payloads omit unsupported client metadata

Compact request payload normalization MUST remove `client_metadata` before
forwarding compact requests upstream.

#### Scenario: Compact strips client metadata

- **WHEN** a compact payload includes `client_metadata`
- **THEN** the upstream compact payload omits it

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream. In this mode, no
`response.failed` synthesis is allowed before `yield` for those upstream frames.

#### Scenario: Raw backend error passthrough

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling sets `enforce_openai_sdk_contract=False`
- **WHEN** the proxy forwards that upstream event in the public stream
- **THEN** the downstream event MUST remain an `error` event
- **AND** `sequence_number`, `error_type`, and message fields from upstream must remain unchanged
- **AND** the event SHOULD NOT be rewritten into `response.failed` in the same stream step

### Requirement: Keep default contract shaping enabled unless explicitly disabled

The proxy MUST keep default contract shaping enabled unless explicitly
disabled. For backward-compatible behavior, when
`enforce_openai_sdk_contract` is omitted or `True`, current error-shaping
behavior MUST remain in place and convert error-type SSE frames as defined by
existing `responses-api-compat` contracts.

#### Scenario: Default public contract still emits response.failed

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling omits `enforce_openai_sdk_contract` or sets it to `True`
- **WHEN** the proxy forwards that upstream event
- **THEN** the downstream event MUST be normalized to `response.failed`

### Requirement: Retry-safe stale WebSocket anchors replay before owner fail-closed handling
When a direct Responses WebSocket request has a prepared retry-safe fresh upstream request body without `previous_response_id`, the service MUST use that replay path for upstream `previous_response_not_found` before applying preferred-owner unavailable handling. This applies when the stale anchor was proxy-injected from session continuity as well as when a client full-resend was classified retry-safe.

#### Scenario: proxy-injected stale anchor has a preferred owner
- **GIVEN** a WebSocket request has `previous_response_id`, a preferred owner account, and `fresh_upstream_request_is_retry_safe` with a no-anchor replay body
- **WHEN** upstream emits `previous_response_not_found` before `response.created`
- **THEN** the service reconnects and replays the prepared no-anchor request
- **AND** it does not rewrite the turn to `previous_response_owner_unavailable`

### Requirement: Codex WebSocket prewarm completions are classified separately
When a direct Responses WebSocket request carries Codex turn metadata with `request_kind: "prewarm"`, the service MUST preserve that request kind in request logs. Empty-output prewarm completions MUST NOT update account success state or previous-response ownership, while still allowing the upstream terminal frame to pass through.

#### Scenario: empty prewarm completion does not look like user turn progress
- **GIVEN** a direct WebSocket request carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **WHEN** upstream emits `response.completed` with zero output tokens
- **THEN** the request log records `request_kind` as `prewarm`
- **AND** the service does not mark the account successful for that completion
- **AND** the service does not remember the response id as a usable previous-response owner

### Requirement: Codex compact requests are bounded by the proxy request budget
When `/backend-api/codex/responses/compact` is called for Codex auto-compaction, the service MUST bound the upstream compact call by the remaining proxy compact request budget even when no explicit upstream compact timeout is configured. The service MUST preserve Codex turn metadata `request_kind` in compact request logs so auto-compaction failures are distinguishable from normal user turns.

#### Scenario: auto-compaction cannot hang past the proxy budget
- **GIVEN** a Codex compact request carries `x-codex-turn-metadata` with `request_kind: "compaction"`
- **AND** no explicit upstream compact timeout is configured
- **WHEN** the service calls upstream
- **THEN** the upstream call receives both connect and total timeout overrides from the remaining compact request budget
- **AND** the request log records `request_kind` as `compaction`

### Requirement: Responses Lite signaling is derived from the normalized body

The service MUST accept Responses and compact requests that include
`X-OpenAI-Internal-Codex-Responses-Lite`, but MUST remove that inbound header
case-insensitively before generic upstream-header forwarding. The service MUST
NOT strip unrelated OpenAI SDK telemetry headers solely because they start with
`x-openai-`.

When an input array contains an item with `type = "additional_tools"`,
instruction normalization MUST leave the entire input array and top-level
`instructions` field unchanged. In particular, neither the tool item nor an
adjacent developer instructions message may be extracted from the native Lite
input prefix. The presence of the `additional_tools` item in the normalized
input array MUST be the authoritative signal that the request uses Responses
Lite.

If compact-request size handling trims oversized conversation history, it MUST
retain the `additional_tools` item and its immediately following developer
instructions message. The resulting compact payload MUST therefore retain the
body signal needed to synthesize the canonical Lite header.

For a Responses Lite body, upstream HTTP Responses and compact requests MUST
include the canonical `x-openai-internal-codex-responses-lite: true` header.
Upstream websocket handshakes MUST omit that header and each websocket
`response.create` body MUST instead include
`client_metadata.ws_request_header_x_openai_internal_codex_responses_lite = "true"`.
For a non-Lite HTTP body, the proxy MUST omit the synthesized HTTP header. A
websocket marker on an incremental frame without the full Lite input prefix MAY
remain only when the same request continuity state previously received
`response.created` for a Lite request derived from `additional_tools` using the
same effective upstream model, and the frame's `previous_response_id` references
the response ID recorded by the most recent such Lite acceptance. A frame
without a `previous_response_id`, or one referencing any other response, MUST
NOT receive trusted Lite treatment. The recorded acceptance ID MUST be the
response ID exposed downstream: when a transparent replay suppresses its
`response.created` and keeps rewriting events to the originally visible
response ID, Lite continuity records that visible ID rather than the hidden
upstream replay ID. The effective model comparison MUST occur
after alias normalization and API-key enforcement, and a merely prepared request
MUST NOT establish or clear trusted Lite continuity. Trusted state MUST update
in upstream request-acceptance order rather than terminal-event completion
order, and acceptance of a non-Lite request MUST NOT clear previously recorded
Lite continuity.
An accepted `generate = false` prewarm derived from an `additional_tools` prefix
MUST establish the same trusted continuity because a later request MAY reuse its
response ID without repeating that prefix.
A transparent fresh full-resend replay that clears `previous_response_id` (for
example after an upstream previous-response miss) severs that linkage, so the
replayed request MUST NOT carry the reserved marker unless its own input
contains the `additional_tools` prefix. Acceptance of such a replay MUST
reflect the replayed body: a marker-stripped replay MUST NOT be recorded as a
Lite acceptance (later frames referencing the replay's response ID are not
trusted), while a replay whose input retains the `additional_tools` prefix
MUST re-establish trusted Lite continuity.
Otherwise, the proxy MUST strip the reserved client-metadata marker. The
HTTP-to-websocket bridge MUST preserve its internally derived canonical marker
when it trims an already-stored input prefix or rebuilds the request during
forwarding or retry, even if the remaining input delta has no `additional_tools`
item.

#### Scenario: Instruction normalization preserves Lite tools and tool history

- **WHEN** a request input contains an `additional_tools` item, developer text,
  custom tool calls, and `custom_tool_call_output` items
- **THEN** top-level `instructions` remains unchanged
- **AND** the developer text, `additional_tools`, custom calls, and outputs all
  remain in their original input order

#### Scenario: HTTP and compact synthesize Lite only from the body

- **WHEN** a normalized HTTP Responses or compact payload contains an
  `additional_tools` input item
- **THEN** the upstream request includes
  `x-openai-internal-codex-responses-lite: true`
- **AND** the original inbound Lite header value is not forwarded verbatim

#### Scenario: Compact trimming retains the Lite prefix

- **GIVEN** an oversized Responses Lite compact input whose tool bundle exceeds
  the normally retained head budget
- **WHEN** compact size handling trims conversation history
- **THEN** the `additional_tools` item and adjacent developer instructions stay
  in their original order
- **AND** the upstream compact request includes the canonical Lite header

#### Scenario: Websocket uses a per-request Lite marker

- **WHEN** a websocket `response.create` payload contains an `additional_tools`
  input item
- **THEN** the upstream websocket handshake omits the Lite header
- **AND** the forwarded `response.create` payload contains the canonical
  per-request Lite client-metadata marker

#### Scenario: HTTP bridge trimming preserves Lite metadata

- **GIVEN** an HTTP Responses Lite request whose stored input prefix contains
  the `additional_tools` item
- **WHEN** the HTTP-to-websocket bridge trims that prefix and forwards only the
  new input delta
- **THEN** the forwarded `response.create` payload still contains the canonical
  per-request Lite client-metadata marker

#### Scenario: Incremental websocket marker requires trusted Lite continuity

- **GIVEN** a websocket request received `response.created` after establishing
  Lite mode from an `additional_tools` prefix for its effective upstream model
- **WHEN** a later same-model incremental frame contains the canonical marker,
  omits the already-known prefix, and its `previous_response_id` references the
  accepted Lite response
- **THEN** the forwarded frame retains the canonical marker
- **BUT WHEN** a request for another model supplies that marker without a Lite
  prefix or trusted same-model continuity
- **THEN** the proxy strips the marker
- **BUT WHEN** a same-model frame supplies that marker without a
  `previous_response_id`, or with one referencing a response other than the
  accepted Lite response
- **THEN** the proxy strips the marker
- **AND** the recorded Lite continuity remains available to later frames that
  do reference the accepted Lite response

#### Scenario: Suppressed-created replay keeps Lite continuity on the visible id

- **GIVEN** a Lite websocket request whose `response.created` was already sent
  downstream when the upstream connection is lost
- **WHEN** the proxy transparently replays the request, suppresses the new
  `response.created`, and rewrites downstream events to the original visible
  response id
- **THEN** a later same-model marker-only frame whose `previous_response_id`
  references the visible response id keeps the trusted marker
- **BUT WHEN** a frame references the hidden upstream replay id instead
- **THEN** the proxy strips the marker

#### Scenario: Fresh replay of a trusted incremental frame drops the marker

- **GIVEN** a trusted marker-only incremental websocket frame whose
  self-contained multi-item input yields a transparent fresh full-resend replay
- **WHEN** upstream reports the referenced previous response as not found and
  the proxy replays the request without `previous_response_id`
- **THEN** the replayed request omits the reserved client-metadata marker
- **AND** the accepted replay is not recorded as a Lite acceptance, so a later
  same-model frame carrying the marker with `previous_response_id` referencing
  the replay's response is not trusted and has its marker stripped
- **BUT WHEN** the replayed input itself contains the `additional_tools` prefix
- **THEN** the replayed request retains the canonical marker
- **AND** the accepted replay re-establishes trusted Lite continuity for later
  frames referencing its response ID

#### Scenario: Accepted Lite prewarm authorizes incremental reuse

- **GIVEN** a same-model Lite prewarm containing `additional_tools` receives
  `response.created`
- **WHEN** Codex reuses that response ID in a later frame with the canonical
  marker but without the already-sent Lite prefix
- **THEN** the forwarded frame retains the canonical marker whether its input
  delta is empty or contains new user input

#### Scenario: Stale inbound headers do not enable a non-Lite request

- **WHEN** an HTTP request has no `additional_tools` input item but includes an
  inbound Lite header
- **THEN** the upstream HTTP request omits the Lite signal
- **AND** existing Codex continuity and unrelated OpenAI telemetry headers are
  preserved

### Requirement: WebSocket tool-output deltas are not fresh-retryable

The service MUST NOT replay a direct WebSocket Responses request as a fresh turn
without the previous-response anchor when it includes `previous_response_id` and
only carries tool output items for tool calls that are not present in the same
payload after an upstream `previous_response_not_found`.

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn

- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses`
  follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`,
  `custom_tool_call_output`, or `apply_patch_call_output` items without their
  matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a
  response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without
  `previous_response_id`

### Requirement: Ultra reasoning effort is aliased to max on the upstream wire

The proxy MUST forward any outbound upstream Responses payload whose `reasoning.effort` resolves to `ultra` — whether requested by the client or injected by API-key reasoning enforcement — with `reasoning.effort: "max"`. `ultra` is a client-plane reasoning effort: GPT-5.6 Sol and Terra advertise it
in their catalog entries, but the reference Codex client rewrites it to `max`
before building the upstream Responses request
(`reasoning_effort_for_request` in codex-rs `core/src/client.rs` at release
rust-v0.144.1); its additional effect (proactive multi-agent mode) is purely
client-side. Source-routed chat-completions
payloads with an enforced `ultra` effort MUST likewise forward `max`. Code
paths that build upstream Responses payloads directly instead of passing
through the proxy request-policy rewrite — such as automation compact pings —
MUST apply the same aliasing before dispatch, while persisted automation
configuration and run history keep the configured client-plane `ultra` value.
`max`
and `xhigh` MUST be forwarded verbatim (no `max` → `xhigh` aliasing exists
upstream).

#### Scenario: Client-requested ultra forwards as max

- **WHEN** a client sends a Responses request for `gpt-5.6-sol` with `reasoning: {"effort": "ultra"}`
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Enforced ultra forwards as max

- **GIVEN** an API key configured with `enforcedReasoningEffort: "ultra"`
- **WHEN** a request is proxied with that API key
- **THEN** the forwarded upstream payload uses `reasoning.effort: "max"`

#### Scenario: Automation compact ping with ultra dispatches max

- **GIVEN** an automation configured with model `gpt-5.6-sol` and reasoning effort `ultra`
- **WHEN** an automation run dispatches its compact ping upstream
- **THEN** the dispatched compact payload uses `reasoning.effort: "max"`
- **AND** the stored automation run history keeps the configured `ultra` effort

#### Scenario: Max is forwarded verbatim

- **WHEN** a client sends a Responses request with `reasoning: {"effort": "max"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "max"`

### Requirement: Source-routed Responses tools are capability-filtered

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST forward `function` tools unchanged and MUST drop non-`function` tools the
source model has not declared support for. A source model declares support in
its `raw_metadata_json`: `"supports_search_tool": true` keeps web-search tools
(`web_search`, including the `web_search_preview` alias), and
`"experimental_supported_tools"` MAY list additional supported tool types.
When only some tools are dropped, a `tool_choice` that references a dropped
tool MUST be removed so the forwarded payload never names a tool that is not
present; `function`-typed choices MUST be preserved. When all tools are
dropped, `tools`, `tool_choice`, and `parallel_tool_calls` MUST be removed
together. Whenever a hosted tool is dropped, `include` entries specific to
that tool type (for example `web_search_call.*` for `web_search`,
`file_search_call.*` for `file_search`, `code_interpreter_call.*` for
`code_interpreter`, and `computer_call_output.*` for computer-use tools) MUST
be pruned from the forwarded payload; non-tool-specific entries (for example
`reasoning.encrypted_content`) MUST be kept, and the `include` field MUST be
removed entirely when pruning empties it. This filtering MUST apply on every
source-routed Responses surface (`/backend-api/codex/responses` and
`/v1/responses`).

#### Scenario: Codex-only tools are dropped for a plain source model

- **GIVEN** a Responses-capable source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `namespace` tool, and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool

#### Scenario: Search-capable source models keep web-search tools

- **GIVEN** a source model whose `raw_metadata_json` sets `"supports_search_tool": true`
- **WHEN** a Responses request with a `function` tool and a `web_search` tool is forwarded to it
- **THEN** the forwarded payload contains both tools
- **AND** a `tool_choice` of `{"type": "web_search"}` is preserved

#### Scenario: tool_choice referencing a dropped tool is removed

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `tool_choice` `{"type": "web_search"}` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload contains no `tool_choice` key

#### Scenario: include entries of a dropped tool are pruned

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request with a `function` tool, a `web_search` tool, and `include` `["web_search_call.action.sources", "reasoning.encrypted_content"]` is forwarded to it
- **THEN** the forwarded payload contains only the `function` tool
- **AND** the forwarded payload's `include` contains only `"reasoning.encrypted_content"`

#### Scenario: Dropping every tool removes the tool-only fields

- **GIVEN** a source model with no tool capability opt-ins
- **WHEN** a Responses request whose tools are all unsupported is forwarded to it
- **THEN** the forwarded payload contains no `tools`, `tool_choice`, or `parallel_tool_calls` keys

### Requirement: Source request overrides apply without clobbering proxy-owned keys

When forwarding a Responses request to an OpenAI-compatible source, the proxy MUST apply the model's `source_request_overrides` from `raw_metadata_json` to
the forwarded payload. The `options` override MUST merge key-wise into any
client-sent `options` object, with override values winning per key. The
overrides MUST NOT change the `model` key (owned by source selection) or the
`stream` key (owned by the proxy's response-handling mode).

#### Scenario: Ollama options are injected into the forwarded payload

- **GIVEN** a source model whose overrides are `{"options": {"num_ctx": 32768}}`
- **WHEN** a Responses request is forwarded to the source
- **THEN** the forwarded payload contains `"options": {"num_ctx": 32768}`

#### Scenario: model and stream overrides are ignored

- **GIVEN** a source model whose overrides contain `"model": "other-model"` and `"stream": false`
- **WHEN** a streaming Responses request for slug `local-model` is forwarded to the source
- **THEN** the forwarded payload keeps `model` as the routed source model
- **AND** the forwarded payload keeps `stream` as `true`

### Requirement: Interrupted tool calls receive synthetic outputs on anchored follow-ups
The service MUST track tool-call items completed by a streamed response that may still require a tool output — `function_call`, `custom_tool_call`, and `apply_patch_call` — together with each call's item type. When a follow-up `response.create` anchors on that completed response via `previous_response_id` and its input omits an output item for a tracked call id, the service MUST prepend a synthetic interrupted output item whose type matches the originating call type (`function_call` -> `function_call_output`, `custom_tool_call` -> `custom_tool_call_output`, `apply_patch_call` -> `apply_patch_call_output`) before forwarding the request upstream. This applies to the direct WebSocket route and to the HTTP responses bridge session path.

#### Scenario: interrupted custom tool call on the WebSocket route
- **GIVEN** a WebSocket `response.create` turn completes with a `custom_tool_call` item whose output was never sent (the turn was interrupted)
- **WHEN** the next `response.create` on the same session references that response via `previous_response_id` without a `custom_tool_call_output` for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input
- **AND** the follow-up does not fail with an upstream `No tool output found for custom tool call` error

#### Scenario: interrupted custom tool call on the HTTP bridge
- **GIVEN** an HTTP bridge session completes a response containing a `custom_tool_call` item whose output was never sent
- **WHEN** the next bridge request anchors on that response id (client-sent or proxy-injected `previous_response_id`) without an output item for the pending call id
- **THEN** the service prepends a synthetic `custom_tool_call_output` item for that call id to the upstream input

#### Scenario: interrupted function call keeps existing output type
- **WHEN** the pending tool call recorded from the previous response is a `function_call`
- **THEN** the synthetic interrupted output item is a `function_call_output` (existing behavior preserved)

#### Scenario: follow-up that carries the tool output is not modified
- **WHEN** the anchored follow-up input already contains a `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` item for a pending call id
- **THEN** the service does not inject a synthetic output for that call id

#### Scenario: injected bridge outputs stay subject to the request size guard
- **GIVEN** an HTTP bridge follow-up whose serialized `response.create` is close to the upstream byte limit
- **WHEN** synthetic interrupted outputs are injected
- **THEN** the service prepares the upstream request from the injected payload so the `response.create` slim/size guard runs against the bytes actually sent upstream
- **AND** an over-limit injected request is rejected locally with `payload_too_large` instead of being forwarded upstream

#### Scenario: stored input context reflects the injected upstream input
- **WHEN** an HTTP bridge follow-up gains synthetic interrupted outputs
- **THEN** the input item count, input fingerprint, and request usage budget recorded for the request are computed from the injected upstream-shaped input, so later full-resend/anchor comparisons on the same bridge session match what upstream actually stored

#### Scenario: unfingerprinted input turns keep the WebSocket continuity anchor
- **GIVEN** a WebSocket turn whose request input yields no prefix fingerprint (a string input — normalized to a single user message at request validation — or an empty input list)
- **WHEN** the response completes with pending tool-call items
- **THEN** the continuity state still records the completed response id and the pending tool-call metadata for all tracked call types, clearing only the prefix count/fingerprint pair
- **AND** a follow-up that anchors on that response id receives the synthetic interrupted outputs instead of leaking the upstream missing-tool-output 400

#### Scenario: local previous-response recovery retry keeps injected outputs
- **GIVEN** an HTTP bridge submit whose payload gained synthetic interrupted outputs and which fails before yielding with a previous-response continuity error
- **WHEN** the local recovery path re-prepares the anchored retry request
- **THEN** the synthetic interrupted outputs are re-injected from the failed session's pending tool-call state, so the recovered submit does not reintroduce the upstream missing-tool-output failure

#### Scenario: replayed apply_patch prefix is trimmed on anchored bridge follow-ups
- **GIVEN** an HTTP bridge follow-up that anchors via `previous_response_id` and replays a prior `apply_patch_call` item (marked as response output) followed by its `apply_patch_call_output`
- **WHEN** the bridge trims the previous-response prefix already covered by the anchor
- **THEN** `apply_patch_call` and `apply_patch_call_output` items are recognized by the trim exactly like the `function_call` and `custom_tool_call` variants, matching the WebSocket route's replay trim

#### Scenario: owner-forward failover recovery injects from local session state when available
- **GIVEN** a multi-instance bridge where an anchored follow-up is forwarded to the remote owner instance and the relay fails before yielding any bytes
- **WHEN** the local instance recovers by rebinding a local bridge session and resubmitting the anchored request
- **THEN** the service injects synthetic interrupted outputs when the rebound local session still holds the pending tool-call state for the anchored response id (for example after ownership flapped back to this instance)

#### Scenario: owner-forward failover recovery without local pending state is a known bounded gap
- **GIVEN** the same owner-forward failure, where the pending tool-call metadata exists only in the remote owner instance's memory (the durable bridge store does not persist pending call ids)
- **WHEN** the local recovery rebinds a fresh session that has no pending tool-call state
- **THEN** the anchored recovery request is resubmitted unmodified, without fabricated tool outputs (matching pre-injection behavior)
- **AND** if upstream rejects it with a missing-tool-output error, the extended classifier masks it as a retryable continuity failure instead of surfacing the raw upstream 400

### Requirement: Missing-tool-output classification covers all tool call variants
The service MUST classify an upstream `invalid_request_error` with `param=input` whose message starts with `No tool output found for function call call_`, `No tool output found for custom tool call call_`, or `No tool output found for apply patch call call_` as a missing-tool-output continuity error, so the existing masking and retry recovery paths engage instead of forwarding the raw upstream 400 downstream.

#### Scenario: custom tool call variant is masked on the HTTP bridge
- **WHEN** upstream emits `invalid_request_error` with `param=input` and message `No tool output found for custom tool call call_x`
- **AND** the pending bridge request carries `previous_response_id`
- **THEN** the service rewrites the error to a retryable `stream_incomplete` continuity failure
- **AND** the raw upstream message and call id are not exposed downstream

### Requirement: Non-message system and developer input items are preserved

When normalizing Responses or compact request `input`, the service MUST only
hoist items that are instruction messages — `system`/`developer`-role items
whose `type` is omitted or `"message"` — into the top-level `instructions`
field. Any `system`/`developer`-role input item carrying any other `type`
value, including item types the service does not model, MUST be forwarded
upstream unchanged and in its original input position. This preservation MUST
hold both when the request is validated and when the request is serialized for
upstream delivery, and it exempts the item from input sanitization: keys such
as `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call`
MUST NOT be stripped from a preserved item. When compact requests exceed the
upstream input budget and
the service trims the input middle, preserved non-message `system`/`developer`
items MUST be treated as trim anchors and retained in the trimmed payload
rather than replaced by the trim marker. When a non-message
`system`/`developer` item is preserved and the request carries no top-level
`instructions` and no hoistable instruction messages, the service MUST default
`instructions` to the empty string so the request still validates and
forwards. Requests whose input contains an `additional_tools` item remain
governed by the Responses Lite rule that leaves the entire input array and
top-level `instructions` unchanged.

#### Scenario: unknown non-message developer input item survives normalization

- **WHEN** a Responses or compact request `input` contains a typed, non-message
  item such as `{"type": "future_directive", "role": "developer", ...}`
  alongside developer instruction messages and user messages
- **THEN** the developer instruction messages are hoisted into `instructions`
- **AND** the `future_directive` item remains in `input` unchanged, in its
  original position
- **AND** the upstream-serialized payload retains the item unchanged

#### Scenario: preserved directive keeps reasoning and tool-call keys

- **WHEN** a Responses or compact request `input` contains a typed,
  non-message `system`/`developer` item carrying keys the interleaved
  reasoning sanitizer strips from message items (such as
  `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`)
- **THEN** the item is retained byte-identical after validation
- **AND** the upstream-serialized payload retains the item byte-identical

#### Scenario: directive-only request without instructions still validates

- **WHEN** a Responses or compact request omits top-level `instructions` and
  its `input` contains only a typed, non-message `system`/`developer` item
  (such as `{"type": "future_directive", "role": "developer", ...}`) alongside
  user messages
- **THEN** the request validates with `instructions` defaulted to `""`
- **AND** the directive item remains in `input` unchanged, including in the
  upstream-serialized payload

#### Scenario: preserved directive survives compact input trimming

- **WHEN** a compact request is large enough to trigger upstream input
  trimming and its input middle contains a typed, non-message
  `system`/`developer` item such as
  `{"type": "future_directive", "role": "developer", ...}`
- **THEN** the trimmed upstream payload retains the item unchanged
- **AND** the item is not replaced by the trim marker

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before

### Requirement: Responses Lite follow-up transformations fail closed

After a request is classified as Responses Lite shaped, the service MUST preserve required Lite state through compact preparation, MUST validate the final transformed compact input against the upstream JSON wire budget, MUST reject policy rewrites to catalog-confirmed non-Lite models, and MUST suppress replayed code-mode side effects without collapsing distinct call identities. These guards MUST NOT weaken the body-derived Lite signal or trusted previous-response linkage rules.

#### Scenario: Oversized compact input keeps the Lite prelude

- **WHEN** compact input trimming is required for a Responses Lite request
- **THEN** every required `additional_tools` item remains in the upstream input
- **AND** typed and role-only system/developer state remains in the upstream input

#### Scenario: Oversized compact input keeps the latest tool item

- **WHEN** compact trimming is required and the latest input item is a tool call or tool output
- **THEN** the latest item remains in the upstream input
- **AND** any matching call or output present in the supplied input is retained with it
- **AND** the service returns `responses_compact_input_too_large` instead of silently dropping the latest item when the required pair cannot fit

#### Scenario: Reused call IDs keep only the required occurrence

- **WHEN** an older tool call and a required state-tool call reuse the same call ID
- **THEN** compact trimming retains the output matched to the required state-call occurrence
- **AND** it does not retain an oversized historical output solely because its earlier call reused that ID

#### Scenario: Exact-budget backtracking drops an optional tool pair together

- **WHEN** optional tool context fits the approximate item budget but trim-marker framing exceeds the exact wire cap
- **THEN** backtracking removes the optional call and its matching output as one group
- **AND** it does not re-add either counterpart while preserving every required item

### Requirement: Compact trimming preserves prioritised historical side effects

The service MUST retain recognised historical side-effect tool calls as bounded
priority context when an oversized compact input is trimmed. It MUST use the
same side-effect classifier as downstream replay
deduplication. This includes code-mode `exec` and `collaboration` wrapper calls
as well as their lower-level tool spellings and recognised parallel batches.

For each retained historical side effect, compact trimming MUST retain its
matching call and output together. The service MUST reserve space for that
complete pair before selecting optional ordinary head or tail context. Required
state anchors and the current required item remain mandatory; if they leave no
room for a historical pair, the service MAY drop that pair together and retain a
trim marker instead.

A recognised side-effect call without a non-empty `call_id` MUST NOT be
retained as a historical side-effect anchor, because it cannot form a verified
call/output pair.

#### Scenario: Code-mode side effect survives an oversized compact input

- **WHEN** an oversized compact input contains a historical custom `exec` or
  `collaboration` call with its matching output outside required state context
- **THEN** the trimmed upstream input retains both the call and its output when
  the pair fits with required state
- **AND** optional ordinary tail context is dropped before that pair

#### Scenario: Historical side-effect pair cannot fit with required state

- **WHEN** required state anchors and the current required item leave no room
  for a historical side-effect call and its matching output
- **THEN** compact trimming drops the entire historical pair
- **AND** it does not retain only one member of that pair

#### Scenario: Side-effect call lacks a usable pair key

- **WHEN** an oversized compact input contains a recognised historical
  side-effect call without a non-empty `call_id`
- **THEN** compact trimming does not preserve that call as a side-effect anchor
- **AND** it does not emit an unpaired historical side-effect call upstream

#### Scenario: Final compact wire expansion is rejected locally

- **WHEN** Unicode escaping, JSON array framing, or image inlining makes the final compact input exceed the upstream limit
- **THEN** the service returns `responses_compact_input_too_large` before an upstream attempt
- **AND** any API-key reservation is released
- **AND** no upstream account is penalized

#### Scenario: Terminal compaction trigger validates before admission

- **WHEN** a streaming Responses request ends with `compaction_trigger` and its derived compact input cannot fit
- **THEN** the service returns the same invalid-client-payload response before admission, reservation, account selection, or upstream compact work

#### Scenario: Enforced non-Lite model rejects Lite input

- **WHEN** API-key policy rewrites Lite-shaped input to a model whose catalog metadata disables Responses Lite
- **THEN** the service rejects the request before any upstream HTTP or websocket attempt

#### Scenario: Replayed code-mode side effects are emitted once

- **WHEN** reconnect replay repeats the same code-mode `exec` or `collaboration` call identity
- **THEN** the downstream client receives that side-effecting call only once

#### Scenario: Distinct code-mode calls remain distinct

- **WHEN** request history has different call IDs with identical code-mode source text and matching outputs
- **THEN** every call and matching output remains in the forwarded history

### Requirement: Reasoning summaries omit blank HTML comment placeholders

Responses reasoning output items and summary delta/part events MUST remove standalone blank HTML comment placeholder lines from `summary_text` before forwarding them to clients, including markers split across delta boundaries. This cleanup applies to both `/backend-api/codex/responses` and `/v1/responses` streamed or collected output item paths. The cleanup MUST be limited to reasoning summary text and MUST NOT rewrite placeholder-free whitespace, assistant-visible message content, inline blank comments, or non-empty HTML comments.

#### Scenario: Codex CLI route does not expose blank comment marker

- **GIVEN** upstream emits a reasoning output item with `summary: [{"type":"summary_text","text":"**Planning**\n\n<!-- -->"}]`
- **WHEN** a Codex CLI client streams `POST /backend-api/codex/responses`
- **THEN** the forwarded reasoning summary text is `**Planning**`
- **AND** the stream does not contain `<!-- -->`

### Requirement: HTTP bridge admission waiters survive upstream replacement

The proxy MUST preserve an HTTP bridge session when its upstream connection
terminates while an unsent request is already waiting for that session's
response-create admission. It MUST fail the requests that were pending on the
terminated upstream but MUST NOT retire, unregister, prune, or release the
retained session while the unsent waiter owns the handoff.

After the waiter acquires admission, the proxy MUST reconnect the retained
session before sending the request. A waiter that has not entered the pending
request queue and has no upstream send timestamp MAY be sent exactly once on
that fresh connection. Hard-affinity sessions MUST retain their account and
continuity ownership during this handoff. If the session was replaced or
unregistered, or reconnection fails, the proxy MUST fail closed without sending
the waiter. Cancelling or failing the last waiter MUST allow the closed session
to retire and release its resources.

#### Scenario: admitted follow-up survives an upstream close

- **GIVEN** one HTTP bridge request is pending upstream
- **AND** a follow-up request is unsent and waiting on the same response-create gate
- **WHEN** the upstream connection closes before the follow-up acquires the gate
- **THEN** the pending request receives its terminal continuity failure
- **AND** the session remains registered and protected from pruning for the waiter
- **AND** the waiter reconnects the retained session and is sent exactly once
- **AND** the waiter does not receive an internal bridge-closed error

#### Scenario: unsafe handoff fails closed

- **GIVEN** an unsent waiter whose prior session was replaced or unregistered
- **OR** the retained session cannot reconnect
- **WHEN** the waiter acquires admission
- **THEN** the waiter is not sent
- **AND** the request receives an explicit retryable proxy error

### Requirement: Selected Codex installation identity is internally consistent

For native Codex requests, the service MUST use an account-specific installation id consistently.
When that id is applied, the service MUST use the same id in `x-codex-installation-id` and in
an existing `x-codex-turn-metadata.installation_id` field on every upstream
Responses transport. Missing, malformed, or non-object turn metadata MUST be
preserved rather than invented or discarded.

#### Scenario: Both canonical metadata carriers are present in a payload

- **WHEN** a native Responses payload contains both installation metadata
  carriers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Both canonical metadata carriers are present in headers

- **WHEN** a native HTTP or WebSocket request carries both installation
  metadata headers
- **AND** the proxy selects a pooled account
- **THEN** both outbound values contain the selected account installation id

#### Scenario: Turn metadata cannot be safely rewritten

- **WHEN** `x-codex-turn-metadata` is malformed JSON, is not a JSON object, or
  does not contain `installation_id`
- **THEN** the service preserves that turn metadata unchanged
- **AND** it still applies the selected account id through the standalone
  installation-id carrier

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the
request has no account-scoped file requirement. A request with an account-
scoped file requirement MUST remain bound to its file owner.

#### Scenario: unanchored bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request is safely replayable before
  `response.created`
- **AND** it has no account-scoped file requirement
- **THEN** the bridge excludes the stalled account before reconnecting

#### Scenario: file-backed bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request requires its file-owner account
- **AND** it is retried before `response.created`
- **THEN** the bridge does not exclude or clear the required file owner

### Requirement: Direct capacity-wait progress follows the downstream stream contract

When direct HTTP/SSE streaming waits for recoverable local account capacity, the proxy MUST emit `codex.keepalive` progress events if the OpenAI SDK stream contract is disabled, regardless of whether the route propagates HTTP errors.
The proxy MUST continue suppressing those non-standard progress events before
startup when both HTTP error propagation and the OpenAI SDK stream contract are
enabled.

#### Scenario: Native image-capable bypass emits capacity progress

- **GIVEN** an image-capable native Codex request bypasses the HTTP responses bridge
- **AND** the route propagates HTTP errors with `enforce_openai_sdk_contract = false`
- **WHEN** direct account selection waits for `account_stream_cap` or `account_response_create_cap` to recover
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"` before capacity is released
- **AND** no upstream response attempt or terminal event occurs before capacity is released
- **AND** account selection retries and the real upstream completion is forwarded after capacity becomes available

#### Scenario: OpenAI SDK startup error remains structured

- **GIVEN** a route propagates HTTP errors with `enforce_openai_sdk_contract = true`
- **WHEN** a local account-capacity wait occurs before stream startup
- **THEN** the proxy MUST NOT emit `codex.keepalive` before startup
- **AND** a terminal local-cap failure remains available to the route's structured HTTP error path

### Requirement: Direct WebSocket replay never mixes numeric response sequences

For direct Responses WebSocket requests, the proxy MUST NOT transparently replay a request on a fresh upstream generation after any finite integer `sequence_number` frame for that request has been successfully sent downstream. When an upstream close would otherwise trigger replay, the proxy MUST settle the failed pending request without emitting frames from a new upstream generation under the existing downstream response id, and MUST close the downstream WebSocket with code 1011 so the client can retry on a fresh transport. When an upstream terminal error would otherwise trigger quota, authentication, security-work, or equivalent replay, the proxy MUST finalize and surface that terminal error without reconnecting. Suppressed frames and non-integer sequence sentinels MUST NOT by themselves disable otherwise-safe replay.

#### Scenario: Sequenced response is interrupted before completion

- **WHEN** a direct WebSocket request has emitted `response.created` or another frame with a finite integer `sequence_number`
- **AND** upstream closes before a terminal response event
- **THEN** codex-lb does not transparently replay that request under the existing downstream response id
- **AND** no lower replay sequence is emitted downstream
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Unsafe replay settles request ownership

- **WHEN** sequenced replay is refused after upstream close
- **THEN** response-create admission, account-local leases, API-key reservations, and request logging are finalized exactly once
- **AND** the failed attempt does not become a successful continuity owner

#### Scenario: Sequenced retryable terminal event is not replayed

- **WHEN** a direct WebSocket request has successfully emitted a finite integer `sequence_number`
- **AND** upstream emits a terminal error that would ordinarily trigger transparent quota, authentication, or security-work replay
- **THEN** codex-lb does not reconnect or resend the request
- **AND** the terminal error is finalized and remains client-visible under the existing error contract

#### Scenario: Sequence-free startup remains replayable

- **WHEN** upstream closes before any numeric sequence-bearing frame has been successfully sent downstream
- **AND** the request otherwise satisfies the existing one-shot replay guard
- **THEN** codex-lb MAY transparently replay the request on a fresh upstream connection

#### Scenario: Suppressed frame does not establish exposure

- **WHEN** codex-lb suppresses an upstream frame before downstream emission
- **AND** the suppressed frame contains a numeric `sequence_number`
- **THEN** that frame does not establish the downstream sequence watermark

### Requirement: Downstream websocket ingress accepts large response.create messages
The server MUST accept client-to-proxy websocket messages on the Responses websocket routes (`/backend-api/codex/responses`, `/v1/responses`) up to a configurable ingress budget before closing the connection at the protocol layer. The default budget MUST be 128 MiB, matching the HTTP responses-path decompressed body cap. The budget MUST be configurable via the `--ws-max-size` CLI flag and the `UVICORN_WS_MAX_SIZE` environment variable, with the CLI flag taking precedence. The server MUST continue to negotiate `permessage-deflate` on the client-facing websocket, and the ingress budget MUST apply to the decompressed message size.

#### Scenario: Oversized response.create reaches the application-level guard
- **WHEN** a client sends a single websocket text message larger than 16 MiB but within the configured ingress budget
- **THEN** the server delivers the message to the application layer instead of closing the connection with `1009 message too big`
- **AND** the application-level oversized-`response.create` handling (historical slimming, then local rejection) applies

#### Scenario: Operator overrides the ingress budget
- **WHEN** the operator starts the server with `--ws-max-size <bytes>` or sets `UVICORN_WS_MAX_SIZE=<bytes>`
- **THEN** the websocket ingress message budget uses the configured value
- **AND** an invalid (non-positive or non-integer) value fails startup with a clear error

### Requirement: Oversized response.create payloads are slimmed or rejected fail-fast before upstream send
When the service prepares a Responses `response.create` request for the upstream websocket, it MUST measure the serialized outbound request size before sending it upstream. If the payload exceeds the upstream websocket budget, the service MUST first attempt to slim only the historical portion of `input` that precedes the most recent user turn: historical inline images MUST be replaced with textual omission notices, and oversized historical tool outputs MUST be replaced with textual omission notices that preserve the item in sequence. If the request still exceeds budget after slimming, the service MUST fail locally with status `400` — not `413` — carrying `error.code = "payload_too_large"`, `error.type = "invalid_request_error"`, and `error.param = "input"`, because the official Codex client treats `400` as a non-retryable invalid-request error surfaced immediately while `413` triggers five full-payload retries followed by a sticky session-wide websocket-to-HTTP transport downgrade.

#### Scenario: Historical inline artifacts are slimmed and the latest user turn is preserved
- **WHEN** a Responses request exceeds the upstream websocket budget because historical inline images or historical oversized tool outputs dominate the serialized `input`
- **AND** replacing those historical artifacts with omission notices reduces the serialized request below budget
- **THEN** the service forwards the slimmed `response.create` upstream
- **AND** it preserves the most recent user turn unchanged

#### Scenario: HTTP Responses route fails locally with 400 when the payload still exceeds budget
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service returns HTTP `400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT allocate or reuse an upstream websocket bridge session for that request

#### Scenario: Websocket Responses route fails locally with a status-400 error event when the payload still exceeds budget
- **WHEN** a websocket `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service emits a websocket error event with `"type": "error"` and `"status": 400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT connect the upstream websocket for that request

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

#### Scenario: Remaining budget constrains all stream attempt timeouts
- **WHEN** account selection, account-capacity recovery, or token refresh leaves only part of the request budget available before a stream attempt starts
- **THEN** the proxy limits the upstream connect timeout, SSE idle timeout, and upstream request total timeout to that same remaining budget
- **AND** the client receives `response.failed` with `upstream_request_timeout` once that budget is exhausted instead of waiting through the full configured stream windows

#### Scenario: Forced refresh retry recomputes all attempt timeouts
- **WHEN** a first stream attempt fails with an authentication error that triggers a forced token refresh and retry
- **THEN** the proxy recomputes the remaining request budget after the refresh
- **AND** the retry attempt reapplies connect, idle, and total timeout limits from that recomputed budget

#### Scenario: Recoverable account-capacity wait is bounded by the request budget
- **WHEN** account selection reports a recoverable retry hint such as temporary rate-limit or stream-capacity exhaustion
- **AND** the streaming request still has remaining request budget
- **THEN** the proxy may wait for at most the smaller of the recovery hint and the remaining request budget before retrying selection
- **AND** if the budget is exhausted before an account becomes available, the request fails through the normal no-account or rate-limit error path instead of starting a fresh full-budget wait

#### Scenario: Local balancer rate-limit exhaustion is not treated as recoverable capacity
- **WHEN** account selection reports the local balancer message `Rate limit exceeded. Try again in Ns`
- **AND** the selection result is a local no-account failure with `no_accounts` or no explicit error code
- **THEN** the proxy does not enter an account-capacity recovery wait from that local retry hint
- **AND** the request returns through the normal no-account or rate-limit error path instead of repeatedly retrying the same local selection failure

#### Scenario: Local account cap selection waits instead of failing immediately
- **WHEN** account selection for a streaming Responses request fails locally with `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy treats the condition as a recoverable account-capacity wait within the request budget
- **AND** it retries account selection after the bounded wait instead of returning an immediate 429
- **AND** permanent `no_accounts` failures remain non-waitable unless they carry a distinct recoverable capacity or upstream quota signal

#### Scenario: Post-selection response-create capacity preserves routing invariants
- **WHEN** a selected account reaches `account_response_create_cap` before downstream output is visible
- **THEN** an unpinned request MUST prefer an eligible alternate account before waiting
- **AND** an owner-bound, file-pinned, or otherwise same-account retry MUST keep or reacquire its stream lease while waiting within the original request budget
- **AND** the same behavior applies after a forced token refresh

#### Scenario: SDK-contract propagated startup errors remain observable
- **WHEN** a route requests HTTP error propagation, enforces the OpenAI SDK stream contract, and waits for local account capacity before startup
- **THEN** the route MUST perform the bounded recovery wait instead of raising the first cap error immediately
- **AND** it MUST NOT emit an account-capacity keepalive before startup succeeds, so a terminal startup error can still use the route's structured error path

#### Scenario: Existing HTTP bridge session waits on submit capacity
- **WHEN** HTTP bridge session submission reaches `account_response_create_cap`
- **THEN** a hard-affinity or file-pinned request MUST wait and retry submission within the bridge request budget
- **AND** a soft-affinity request MUST retain its existing alternate-session reroute behavior before waiting on the saturated session

#### Scenario: WebSocket account selection waits on local caps
- **WHEN** downstream WebSocket account selection returns `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy MUST emit a `codex.keepalive` with status `waiting_for_account_capacity`
- **AND** retry selection within the original WebSocket request budget
- **AND** return the original local-cap error if that budget is already exhausted

### Requirement: Streaming account-capacity waits keep clients alive
When a streaming Responses request waits for temporary account capacity to recover before account selection can continue, the proxy MUST emit downstream progress events during the wait. HTTP/SSE and HTTP bridge streams MUST emit `codex.keepalive` events with `status = "waiting_for_account_capacity"`, request id, elapsed wait seconds, and retry-after seconds when known. HTTP bridge streams MAY also emit `response.in_progress` to satisfy OpenAI Responses stream parsers before later terminal events. WebSocket clients MUST receive equivalent `codex.keepalive` JSON messages. These progress events MUST NOT expose account emails, API keys, raw affinity keys, prompt content, or request payloads. Contract-shaped streams remain subject to the direct capacity-wait progress requirement, which suppresses non-standard progress events before startup when both HTTP error propagation and the OpenAI SDK stream contract are enabled.

#### Scenario: HTTP/SSE capacity wait emits keepalive
- **WHEN** `/v1/responses` streaming account selection can recover after a retry hint
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"`
- **AND** includes the request id, waited seconds, and bounded retry-after seconds

#### Scenario: HTTP bridge capacity wait preserves parser progress
- **WHEN** an HTTP responses bridge request waits for session creation or account selection capacity
- **THEN** the bridge stream emits a capacity-wait keepalive
- **AND** emits OpenAI-compatible in-progress events when needed so downstream Responses stream parsers do not time out before the terminal response

#### Scenario: WebSocket capacity wait emits JSON keepalive
- **WHEN** a WebSocket Responses request waits for account capacity recovery
- **THEN** the downstream WebSocket receives a JSON `codex.keepalive` message with `status = "waiting_for_account_capacity"`
- **AND** the connection remains open until selection retries, the request budget expires, or the client disconnects

### Requirement: Downstream-HTTP upstream transport follows a configurable policy

When a downstream HTTP/SSE request (`request_transport == "http"`) resolves its base upstream transport to `"websocket"`, the proxy MUST decide the final upstream transport using the configured `http_downstream_transport_policy`, after all higher-precedence rails have been applied, and the policy MUST NOT affect native WebSocket clients (`request_transport == "websocket"`), which keep their dedicated upstream WebSocket path.

Precedence (highest first), evaluated before the policy:

1. An explicit `upstream_stream_transport` override of `"http"` or
   `"websocket"` wins outright.
2. Oversized-payload bypass and image / image-generation bypass force
   upstream HTTP.
3. The effective policy (per-API-key `transport_policy_override` when
   set, otherwise the global `http_downstream_transport_policy`) decides.

Policy values and behavior:

- `always_http` (and its alias `pinned`): the request MUST be sent over
  upstream HTTP `POST`, preserving the legacy unconditional pin.
- `always_websocket`: the request MUST keep upstream WebSocket whenever
  the base transport resolved to `"websocket"` without replacing a base
  `"auto"` transport mode with a hard `"websocket"` override.
- `smart` (default): the request MUST keep upstream WebSocket **iff** at
  least one sticky-continuation signal is present on the request, and
  MUST otherwise fall back to upstream HTTP. The sticky-continuation
  signals are:
  - a non-null `previous_response_id` on the request payload, **OR**
  - a `prompt_cache_key` present on the request model, **OR**
  - a Codex session header (`session_id`, `x-codex-session-id`, or
    `x-codex-conversation-id`), **OR**
  - an `x-codex-turn-state` continuity header.

When a policy decision keeps upstream WebSocket, the proxy MUST preserve
the configured/base downstream transport mode passed to the upstream
client. In particular, a base `"auto"` mode MUST remain `"auto"` so the
existing WebSocket-handshake rejection fallback to upstream HTTP remains
available. The policy MAY force a concrete transport override only when
the decision is to downgrade to upstream HTTP.

The per-API-key `transport_policy_override`, when non-null, MUST be used
as the effective policy for requests authenticated by that key and MUST
take precedence over the global default. A null override MUST fall
through to the global `http_downstream_transport_policy`.

#### Scenario: single-shot downstream-HTTP request falls back to HTTP under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport resolves to `"websocket"`
- **AND** a downstream HTTP request carries no `previous_response_id`, no
  `prompt_cache_key`, no Codex session header, and no `x-codex-turn-state`
  header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: sticky downstream-HTTP request keeps WebSocket under smart policy

- **GIVEN** `http_downstream_transport_policy` is `"smart"` and the base
  upstream transport mode is `"auto"` and resolves to `"websocket"`
- **AND** a downstream HTTP request carries any one of
  `previous_response_id`, `prompt_cache_key`, a Codex session header, or
  an `x-codex-turn-state` header
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`
- **AND** an upstream WebSocket handshake rejection status eligible for
  auto fallback MUST transparently retry over upstream HTTP

#### Scenario: always_http policy preserves the legacy pin

- **GIVEN** `http_downstream_transport_policy` is `"always_http"` (or
  `"pinned"`) and the base upstream transport resolves to `"websocket"`
- **WHEN** a downstream HTTP request resolves the upstream transport,
  regardless of sticky signals
- **THEN** the request MUST be sent over upstream HTTP `POST`

#### Scenario: always_websocket policy never downgrades sticky-less HTTP

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
  and the base upstream transport mode is `"auto"` and resolves to
  `"websocket"`
- **WHEN** a downstream HTTP request with no sticky signals resolves the
  upstream transport
- **THEN** the request MUST keep upstream WebSocket without converting
  the downstream transport mode from `"auto"` to `"websocket"`

#### Scenario: per-key override wins over the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has
  `transport_policy_override = "always_http"`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`,
  because the per-key override takes precedence

#### Scenario: null per-key override follows the global policy

- **GIVEN** the global `http_downstream_transport_policy` is `"smart"`
- **AND** the authenticating API key has `transport_policy_override =
  null`
- **WHEN** a sticky downstream HTTP request authenticated by that key
  resolves the upstream transport
- **THEN** the request MUST keep upstream WebSocket, following the global
  `smart` policy

#### Scenario: explicit websocket override still beats the policy

- **GIVEN** `upstream_stream_transport` is explicitly `"websocket"`
- **WHEN** a single-shot downstream HTTP request with no sticky signals
  resolves the upstream transport under any policy
- **THEN** the explicit override MUST win and the request MUST use
  upstream WebSocket

#### Scenario: oversized payload bypass still forces HTTP under always_websocket

- **GIVEN** `http_downstream_transport_policy` is `"always_websocket"`
- **AND** the serialized request payload exceeds the WebSocket frame
  budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over upstream HTTP `POST`, because the
  oversized-payload bypass has higher precedence than the policy

#### Scenario: native WebSocket clients are unaffected by the policy

- **GIVEN** any value of `http_downstream_transport_policy`
- **WHEN** a native WebSocket client (`request_transport == "websocket"`)
  streams a request
- **THEN** the client MUST keep its dedicated upstream WebSocket path and
  the policy MUST NOT downgrade it to HTTP

### Requirement: Request-scoped Codex metadata survives HTTP-to-WebSocket bridging

When an HTTP Responses request is translated into an upstream WebSocket `response.create` frame, the service MUST project nonblank `x-codex-turn-metadata`, `x-openai-subagent`, `x-codex-parent-thread-id`, and `x-codex-window-id` compatibility headers into that frame's `client_metadata`. This projection MUST happen for every request, including requests multiplexed over a reused upstream socket. A metadata value already supplied in the request body MUST remain authoritative over the compatibility header, and header matching MUST be case-insensitive.

#### Scenario: Reused bridge session receives a subagent turn

- **GIVEN** a parent HTTP request has opened an upstream Responses WebSocket
- **WHEN** a subagent HTTP request reuses that socket with subagent, parent-thread, and child-window headers
- **THEN** the subagent request's `response.create.client_metadata` contains those values
- **AND** the earlier parent frame retains its own window metadata
- **AND** no value is inherited solely from the socket handshake

#### Scenario: Body metadata remains canonical

- **WHEN** a request body and compatibility header provide different values for the same Codex metadata key
- **THEN** the upstream `response.create.client_metadata` retains the body value

### Requirement: Compact routing honors turn-state affinity

When a compact request carries a nonblank `x-codex-turn-state`, the service MUST classify that value as Codex-session affinity before considering a session header, prompt-cache affinity, or sticky-thread affinity. This precedence MUST apply even when generic Codex session-header affinity is disabled, matching the normal Responses path.

#### Scenario: Turn-state-only compact remains on the turn owner

- **GIVEN** a Responses turn established an account mapping for an `x-codex-turn-state` value
- **AND** another account becomes preferable under the non-sticky routing strategy
- **WHEN** `/responses/compact` carries only that turn-state continuity value
- **THEN** the compact request is routed to the account that owns the turn-state mapping

#### Scenario: Turn-state overrides less-specific affinity

- **WHEN** a compact request carries turn-state, session-header, and prompt-cache keys
- **THEN** its affinity key is the turn-state value
- **AND** its affinity kind is Codex session

### Requirement: Namespaced side-effect replay dedupe preserves call identity

For a namespaced side-effect function or custom-tool call, the service MUST use the call's namespace and call ID as part of downstream and replayed-history deduplication identity. An exact replay with the same namespace, name, call ID, and canonical arguments MUST remain suppressed. Calls with different namespaces or different nonblank call IDs MUST remain distinct, even when their names and canonical arguments match, and their matching outputs MUST remain in forwarded history.

Flat legacy side-effect calls MAY continue to use argument-based replay identity so reconnects that change only a call ID do not repeat shell, patch, or terminal side effects.

#### Scenario: Distinct namespaced spawns use identical arguments

- **WHEN** two `collaboration.spawn_agent` calls have identical arguments and different call IDs
- **THEN** both calls are forwarded
- **AND** both matching outputs remain in replayed request history

#### Scenario: Exact namespaced call is replayed after reconnect

- **WHEN** reconnect replay emits the same namespaced call ID and canonical arguments under a new response ID
- **THEN** the service suppresses the replayed downstream call

#### Scenario: Equal call identity appears in different namespaces

- **WHEN** two side-effect calls share a name, call ID, and arguments but have different namespaces
- **THEN** the service treats them as distinct calls

### Requirement: Compact requests preserve scoped turn-state ownership

When a compact request contains a real client-supplied `x-codex-turn-state`, the system MUST resolve the token only in the requesting API key scope and select only that owner account. If the owner cannot be resolved or selected, the request MUST fail closed and MUST NOT fall back to a generic sticky or load-balanced account. Proxy-synthesized first-turn placeholders (the `turn_*` / `http_turn_*` values codex-lb injects when the client did not supply one) are not real continuity tokens until registered as bridge aliases; an unregistered placeholder MUST NOT block file-owner routing, but a registered placeholder MUST still resolve to its owner account.

#### Scenario: Token belongs to the requesting API key

- **GIVEN** an active turn-state owner exists for the requesting API key
- **WHEN** the client submits a compact request with that token
- **THEN** compact selection is constrained to that owner account

#### Scenario: Unscoped sticky state cannot supply a turn-state owner

- **GIVEN** a turn-state token has no owner in the requesting API-key-scoped local or durable bridge indexes
- **WHEN** an unscoped sticky-session mapping exists for the same token
- **THEN** compact owner resolution fails closed
- **AND** the unscoped sticky-session mapping is not consulted

#### Scenario: Token belongs to a different API key or is unavailable

- **GIVEN** the token has no owner in the requesting API key scope
- **WHEN** the client submits a compact request with that token
- **THEN** the request fails with `turn_state_owner_unavailable`
- **AND** no generic account is selected

#### Scenario: Registered synthesized placeholder belongs to the requesting API key

- **GIVEN** a proxy-synthesized `http_turn_*` token has been registered as a bridge alias
- **WHEN** the client later submits a compact request with that token
- **THEN** compact selection is constrained to the registered owner account

#### Scenario: Synthesized first-turn placeholder does not override file-owner routing

- **GIVEN** the request carries only a proxy-synthesized `x-codex-turn-state`
- **AND** the payload references an `input_file.file_id` pinned to an account
- **WHEN** the client submits the compact request
- **THEN** compact routing may use the pinned file owner
- **AND** the synthesized placeholder does not trigger `turn_state_owner_unavailable`

### Requirement: Collected failures retain upstream turn-state metadata

The system MUST copy a real `x-codex-turn-state` received in a `response.metadata` event into the HTTP headers of a collected response, including when the later terminal event is `response.failed`.

#### Scenario: Metadata precedes a failed response

- **GIVEN** a collected response stream emits turn-state metadata
- **AND** the terminal response is failed
- **THEN** the returned HTTP error includes the captured turn-state header

### Requirement: WebSocket incomplete responses preserve the upstream reason in request logs

When an upstream Responses WebSocket terminal `response.incomplete` event contains a non-empty string at `response.incomplete_details.reason`, the service SHALL persist the request log with status `error` and SHALL preserve that reason as both `error_code` and `error_message`. The terminal event sent to the downstream client and the account-health treatment of an incomplete response SHALL remain unchanged.

#### Scenario: max-output limit is identifiable in a WebSocket request log

- **WHEN** the upstream emits `response.incomplete` with
  `incomplete_details.reason` equal to `max_output_tokens`
- **THEN** the corresponding WebSocket request log has status `error`,
  `error_code` equal to `max_output_tokens`, and `error_message` equal to
  `max_output_tokens`
- **AND** the account is not marked unhealthy solely because of that
  incomplete event

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Audio-transcriptions-compatible sources MAY
serve `/v1/audio/transcriptions`. Codex-native compaction, file upload,
control-plane, and websocket bridge paths MUST remain subscription-backed unless
a later requirement explicitly defines OpenAI-compatible source behavior for
those paths.

#### Scenario: Chat completions routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares chat-completions support
- **AND** the authenticated API key is allowed to use that source/model
- **WHEN** the client calls `POST /v1/chat/completions` with that model
- **THEN** the proxy forwards the request to the source's configured base URL
  using the source's upstream API key

#### Scenario: Codex-native Responses route uses Responses-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares Responses support
- **AND** it exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `deepseek-v4-flash`
- **THEN** the proxy forwards the request to that source's Responses endpoint

#### Scenario: Chat-only source is not used for Codex-native Responses route

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `local-coder`
- **THEN** the request is not routed to that source
- **AND** subscription-backed Codex routing rules continue to apply

#### Scenario: Compaction request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /backend-api/codex/responses` for that model whose
  input contains a `compaction_trigger` item
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription-backed Codex compaction path instead

#### Scenario: File-referencing request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `/backend-api/codex/responses` or `/v1/responses` for that
  model whose input references an uploaded `input_file`/`input_image` `file_id`
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription path so the account-scoped file pin is honored

#### Scenario: Audio transcription routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares audio transcriptions support
- **AND** it exposes model `whisper-large-v3`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with multipart
  field `model=whisper-large-v3`
- **THEN** the proxy forwards the multipart request to the source's
  `/audio/transcriptions` endpoint
- **AND** the request uses the source's upstream API key

#### Scenario: Non-source transcription model keeps subscription validation

- **GIVEN** no audio-transcriptions-compatible source exposes model `gpt-4o-mini`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with
  `model=gpt-4o-mini`
- **THEN** the proxy returns the existing unsupported transcription model error

### Requirement: Source-routed chat payloads are sanitized before forwarding

Source-routed `/v1/chat/completions` requests SHALL forward the client's
OpenAI-compatible payload with the following sanitization applied to the
outbound body:

- An empty `tools` array MUST be omitted, together with `tool_choice` and
  `parallel_tool_calls`, so tool-less requests reach the source without
  tool-calling artifacts.
- Non-standard reasoning toggles (`include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, and `reasoning_effort`) MUST be stripped
  unless the source model's catalog entry opts into reasoning via
  `raw_metadata_json` containing `"supports_reasoning": true`.
- An API key's enforced reasoning effort MAY still be applied after
  sanitization; explicit operator policy overrides the default strip.

#### Scenario: Empty tools array is not forwarded

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** a client calls `POST /v1/chat/completions` for that model without
  tools (or with `"tools": []`) and `"tool_choice": "none"`
- **THEN** the body forwarded to the source contains no `tools`, `tool_choice`,
  or `parallel_tool_calls` keys

#### Scenario: Reasoning toggles are stripped for non-reasoning source models

- **GIVEN** a source model whose catalog entry does not declare
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, or `reasoning_effort` in the request
- **THEN** none of those keys appear in the body forwarded to the source

#### Scenario: Catalog opt-in preserves reasoning toggles

- **GIVEN** a source model whose `raw_metadata_json` contains
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning: true`
- **THEN** the forwarded body preserves the client's reasoning fields

### Requirement: Source-routed audio transcriptions preserve OpenAI-compatible multipart semantics

Source-routed `/v1/audio/transcriptions` requests SHALL forward the inbound
audio file and non-file multipart fields to the selected source's
`/audio/transcriptions` endpoint. The proxy MUST use the stored source API key
for upstream authorization and MUST NOT forward the downstream client's
authorization credential. JSON and non-JSON successful upstream response bodies
SHALL be returned to the client with the upstream content type when present.

#### Scenario: Text transcription response passes through

- **GIVEN** an enabled OpenAI-compatible source exposes model `whisper-large-v3`
- **AND** the client requests `response_format=text`
- **WHEN** the source returns a plain text response
- **THEN** the proxy returns that response body without requiring JSON parsing

#### Scenario: Limited key requires token usage

- **GIVEN** an API key has token or cost limits
- **AND** a source-routed audio transcription response has no token-compatible
  usage fields
- **AND** the source model declares no per-minute audio rate
- **WHEN** the upstream source returns a successful transcription response
- **THEN** the proxy releases the reservation
- **AND** returns `usage_unavailable` instead of allowing unaccounted limited-key usage

### Requirement: Audio transcription sources MAY bill by duration

The proxy SHALL support per-minute audio billing for source models that
declare an `audio_per_minute` rate. When the rate is set and a source-routed
`/v1/audio/transcriptions` response carries a positive audio duration
(top-level `duration` seconds, or a `usage.seconds`/`usage.duration` fallback),
the proxy MUST settle cost as `duration_minutes * audio_per_minute` with zero
tokens, and MUST record that cost on the request log and against the API key's
`cost_usd` limit. Duration billing MUST take precedence over token pricing on
the transcription route. A model with no `audio_per_minute` rate MUST fall back
to token-usage settlement.

#### Scenario: Duration-priced model settles cost from audio length

- **GIVEN** an audio-transcriptions source model with `audio_per_minute = 0.30`
- **AND** an API key with a `cost_usd` limit
- **WHEN** a transcription response reports `duration = 120` seconds and no token usage
- **THEN** the API-key reservation is finalized with 0 tokens and $0.60 cost
- **AND** the request log records `cost_usd = 0.60`

#### Scenario: Duration billing does not require token usage for limited keys

- **GIVEN** an audio-transcriptions source model with an `audio_per_minute` rate
- **AND** an API key with token or cost limits
- **WHEN** a transcription response carries a positive duration but no token usage
- **THEN** the request succeeds and settles from duration
- **AND** the proxy does not return `usage_unavailable`

### Requirement: Upstream Responses payloads omit client-omitted request fields

The service MUST NOT emit top-level request fields the client omitted onto
upstream Responses payloads when the field's absence is meaningful upstream.
In particular, the proxy MUST NOT synthesize a top-level `"tools": []` from
the request model's default for clients that did not send the `tools` field,
on any upstream transport (websocket `response.create` frames, HTTP-bridge
bodies, and direct HTTP stream requests). An explicit client-sent
`"tools": []` MUST be forwarded as `[]`. `tool_choice` and
`parallel_tool_calls` MUST be forwarded only when the client sent them;
an explicit client-sent `parallel_tool_calls: false` MUST reach upstream.
The OpenAI-compatible `/v1/responses` conversion MUST propagate `tools`
omission into the native request so both routes behave identically.
Field omission MUST survive every re-serialization hop: the multi-instance
owner-forward body (internal bridge forward) MUST NOT contain fields the
client omitted, the owner instance receiving a forwarded request MUST NOT
re-mark `tools` as explicitly set, and model-source Responses egress payloads
MUST likewise omit fields the client never sent. The owner forward MUST carry
a v2 signature (`x-codex-bridge-signature-v2`) computed over the same
forwarding serialization that is posted as the body, and the forwarding
origin MUST NOT relay externally supplied `x-codex-bridge-*` headers. The
receiving instance MUST treat the v2 signature as authoritative only when it
validates: a valid v2 signature accepts the forward (proving the received
body was not rewritten, including an injected `"tools": []`); an absent or
invalid v2 header falls back to the legacy signature verification; the
forward is rejected only when neither verifies. Mere v2-header presence MUST
NOT block a legacy-signed forward, because pre-v2 origins relay unknown
inbound bridge headers verbatim and an external client could otherwise deny
legitimate forwards by planting a garbage v2 header. For rolling-upgrade
compatibility the origin MUST also keep sending the legacy signature headers
(computed over the plain dump with the synthesized `"tools": []`) so pre-v2
owners verify unchanged. ROLLOUT SHIM: the legacy header emission and the
legacy fallback are a one-release compatibility shim and MUST be removed in a
follow-up change once fleets are homogeneous on a v2-signing release (grep
for `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`); while the shim is
active the legacy fallback is exactly as strong as the pre-v2 scheme (a
body-only rewrite injecting `"tools": []` into a dual-signed forward
downgrades to the legacy digest and verifies), and removing the shim restores
strict v2-only rejection.

#### Scenario: Responses Lite request reaches upstream without a tools key

- **WHEN** a `/backend-api/codex/responses` request omits top-level `tools`
  and carries its tool bundle in an `additional_tools` input item
- **THEN** the upstream websocket `response.create` frame contains no
  top-level `tools` key
- **AND** the HTTP-bridge request body contains no top-level `tools` key

#### Scenario: Explicit empty tools array is forwarded

- **WHEN** a client sends `"tools": []` explicitly
- **THEN** the upstream payload contains `"tools": []`

#### Scenario: Unset optional tool fields stay absent

- **WHEN** a client omits `tool_choice` and `parallel_tool_calls`
- **THEN** the upstream payload contains neither field

#### Scenario: Owner-forwarded request keeps tools omitted across instances

- **WHEN** a request that omits top-level `tools` is forwarded to its owner
  instance over the internal HTTP bridge (multi-instance owner forward)
- **THEN** the owner-forward request body contains no top-level `tools` key
- **AND** the owner instance parses the forwarded body without marking
  `tools` as explicitly set, so its upstream payload contains no top-level
  `tools` key
- **AND** the owner-forward signature still verifies on the owner instance

#### Scenario: Owner-forward v2 signature covers the posted body

- **WHEN** an owner-forward body that omitted top-level `tools` is rewritten
  in transit to carry an injected explicit `"tools": []`
- **THEN** the v2 signature verification fails
- **AND** absent a valid legacy shim signature, the owner instance rejects
  the forwarded request with an invalid bridge-forward-signature error
  instead of re-marking `tools` as explicitly set
- **AND** generic body rewrites outside the synthesized-tools equivalence
  class fail both digests and are rejected even while the shim headers are
  present

#### Scenario: Mixed-version fleets keep verifying during a rolling upgrade

- **WHEN** an updated origin forwards a dual-signed tools-less body to an
  owner still running pre-v2 code
- **THEN** the legacy signature header matches the pre-v2 owner's
  recomputation over the plain dump, so the forward verifies unchanged
- **WHEN** a pre-v2 origin forwards a legacy-signed body (no v2 header) to
  an updated owner
- **THEN** the updated owner falls back to legacy verification and accepts
  the forward

#### Scenario: Spoofed v2 header does not deny legacy forwards

- **WHEN** a legacy-signed forward from a pre-v2 origin arrives carrying a
  garbage `x-codex-bridge-signature-v2` header that an external client
  planted (pre-v2 origins relay unknown inbound bridge headers verbatim)
- **THEN** the updated owner treats the invalid v2 signature as
  non-authoritative, falls back to legacy verification, and accepts the
  forward
- **AND** an updated origin strips externally supplied `x-codex-bridge-*`
  headers before forwarding, so its own forwards never relay a planted
  header

#### Scenario: Model-source Responses egress omits unsent tools

- **WHEN** a Responses request that omits top-level `tools` is routed to an
  openai-compatible model source
- **THEN** the payload sent to the model source contains no top-level
  `tools` key

### Requirement: Client tool entries are forwarded byte-preserved

The service MUST forward client-sent top-level `tools` entries to upstream
byte-preserved: the tool array order, per-object key order, unknown keys
(including unknown tool types such as `namespace` entries and non-standard
schema markers), and array-value order (for example `parameters.required`)
MUST reach upstream exactly as the client sent them. Tool canonicalization
(array sorting and recursive key sorting) MUST be used only for prompt-cache
affinity and observability hashing and MUST NOT mutate the outgoing payload.
The affinity/observability hash MUST remain insensitive to tool array order
and object key order.

#### Scenario: Reserved namespace tool survives byte-identical

- **WHEN** a client sends top-level `tools` containing a reserved
  `{"type": "namespace", "name": "collaboration", ...}` entry with nested
  function entries, `strict: false`, unknown property markers, and a
  non-alphabetical `required` array
- **THEN** the upstream `response.create` frame serializes that `tools` array
  byte-identical to the client's serialization

#### Scenario: Affinity hash ignores tool ordering

- **WHEN** two requests differ only in tool array order or tool object key
  order
- **THEN** their tools affinity/observability hash is identical
### Requirement: Streaming events are parsed once and re-serialized only when modified

Within each streaming layer (core client consumer, streaming mixin, bridge upstream reader, /v1 normalizers), an SSE event's JSON payload MUST be parsed at most once and reused by that layer's consumers, and an event that no consumer modified MUST NOT be re-serialized by the /v1 normalizers. Event framing, payload contents, dedupe/rewrite semantics, and error normalization MUST be unchanged.

#### Scenario: Unmodified events pass through the /v1 normalizer verbatim

- **GIVEN** a canonical stream event that no normalizer branch rewrites
- **WHEN** the /v1 response normalizer processes it
- **THEN** the original block is yielded byte-identically without re-serialization

#### Scenario: Tool-call rewrite reuses the parsed event on the no-change path

- **GIVEN** an event without duplicate parallel tool calls
- **WHEN** the rewrite step runs with the caller's parsed event
- **THEN** it returns the original line, payload, and event without re-parsing

#### Scenario: Rewritten events stay consistent

- **WHEN** the rewrite step removes duplicate tool calls
- **THEN** the returned line, payload, and validated event all reflect the rewritten content
