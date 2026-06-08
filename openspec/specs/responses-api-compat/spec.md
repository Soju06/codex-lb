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
When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before surfacing `stream_incomplete` to those pending requests.

#### Scenario: websocket closes before pending responses complete
- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** the websocket closes before a terminal response event is observed
- **THEN** the pending request fails with `stream_incomplete`
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
When a direct WebSocket `response.create` request includes both `previous_response_id` and a full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

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

When `classify_upstream_failure` observes an upstream error envelope whose `code` is `overloaded_error`, the system MUST treat it as `retryable_transient` regardless of the accompanying HTTP status. Streamed Responses API traffic can deliver the overload envelope on a connection that has already returned HTTP 200, so a 5xx-only heuristic is insufficient to drive account fail-over and bounded retry.

#### Scenario: `overloaded_error` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the failover layer is eligible to retry the request or fail over to another account instead of returning a non-retryable error to the client

#### Scenario: `overloaded_error` with a 5xx status remains retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` is 500, 502, 503, or 504
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the result is the same as the no-status path, so the 5xx fallback heuristic is not the only signal driving the decision

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
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps.

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

### Requirement: Hard continuity owner lookup fails closed
When a request depends on hard continuity ownership, the service MUST fail closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT continue with local recovery or account selection that bypasses hard owner enforcement.

#### Scenario: websocket previous-response owner lookup errors
- **WHEN** a websocket or HTTP fallback follow-up request includes `previous_response_id`
- **AND** owner lookup errors prevent the proxy from determining the required owner account
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue the request on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys
- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state, explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent the proxy from proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current replica

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
(`minimal`, `low`, `medium`, `high`, `xhigh`, `extra`, `fast`, `priority`, `reasoning`, `thinking`) to supported GPT-5 family slugs. The alias
resolver MUST match longer qualified canonical slugs before shorter family prefixes so aliases such as `gpt-5.4-mini-high` and `gpt-5.3-codex-fast` normalize
to the intended model. Unknown suffix tokens MUST leave the requested model unchanged.

#### Scenario: Qualified mini model alias normalizes reasoning

- **WHEN** a client sends a Responses request with `model: "gpt-5.4-mini-high"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.4-mini"`
- **AND** the forwarded upstream request uses `reasoning.effort: "high"`

#### Scenario: Qualified codex model alias normalizes service tier

- **WHEN** a client sends a Responses request with `model: "gpt-5.3-codex-fast"`
- **THEN** the forwarded upstream request uses `model: "gpt-5.3-codex"`
- **AND** the forwarded upstream request uses `service_tier: "priority"`

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
The service SHALL remove `tools`, `tool_choice`, and `parallel_tool_calls` from compact request payloads before calling the upstream compact endpoint.

#### Scenario: compact request reuses a full Responses payload shape
- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `tools`, `tool_choice`, or `parallel_tool_calls`
- **THEN** the proxy drops those fields before the upstream compact request
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

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files`, when an in-memory pin for that `file_id` is still live. Stronger affinity signals MUST take precedence over the file_id pin: an explicit `prompt_cache_key`, a session header (`StickySessionKind.CODEX_SESSION`), a turn-state header, or a `previous_response_id` MUST keep their existing routing semantics.

When multiple `file_id`s are referenced and several are pinned, the most-recently-pinned one MUST be preferred (with a deterministic lexicographic tie-break on `file_id`).

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a`
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` and has no stronger affinity
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: prompt_cache_key overrides the file_id pin

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST follow the prompt-cache affinity for routing and MUST NOT use the file_id pin

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty accepted session header, the service MUST use that value as the routing affinity key for upstream account selection. If the request lacks a client-supplied `prompt_cache_key`, the service MUST derive and attach a stable `prompt_cache_key` before upstream forwarding so account affinity and upstream prompt-cache routing can coexist. Accepted session headers are `session_id`, `x-codex-session-id`, and `x-codex-conversation-id`, in that priority order.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing
- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `prompt_cache_key`
- **THEN** the routing decision still uses durable `codex_session` affinity for account selection
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

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
request receives an upstream `401 invalid_api_key` response for the selected
account, the proxy MUST attempt one forced token refresh and retry the compact
request on that same account. If the refreshed retry also returns `401`, the
proxy MUST classify and record the account failure, exclude that account from
the current compact request, and try another eligible account when one is
available. The proxy MUST NOT surface the repeated account-local `401` to the
compact client before exhausting eligible accounts.

#### Scenario: Refreshed compact auth failure uses another account

- **GIVEN** at least two accounts are eligible for a compact request
- **AND** the selected account returns `401 invalid_api_key` for compact before and after a forced refresh
- **WHEN** another eligible account can complete the compact request
- **THEN** the downstream compact response succeeds from the second account
- **AND** the selected account is excluded from further attempts for that compact request

#### Scenario: Compact 401 is not a generic same-contract retry

- **WHEN** low-level compact transport receives HTTP 401 from upstream
- **THEN** the service-level auth refresh/failover path handles it
- **AND** the low-level compact transport does not mark it as a generic same-contract transport retry

### Requirement: Pre-visible proxy auth failures fail over after forced refresh

The proxy MUST treat repeated account-local authentication failures as
per-request account failures before any downstream-visible output is emitted.
When a proxy request on a non-compact surface retries with a refreshed token and
the refreshed retry still returns upstream `401 invalid_api_key`, the proxy MUST
classify and record the selected account failure, exclude that account from the
current request, and try another eligible account when one is available. The
proxy MUST preserve the existing no-replay rule after downstream-visible stream
or websocket output has been emitted.

#### Scenario: Pre-visible streaming auth failure uses another account

- **GIVEN** at least two accounts are eligible for a streaming responses request
- **AND** the selected account returns `401 invalid_api_key` before downstream-visible output
- **WHEN** another eligible account can complete the request
- **THEN** the downstream stream succeeds from another account
- **AND** the selected account is excluded from further attempts for that request

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
