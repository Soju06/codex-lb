# Responses API Compatibility

## Purpose

Ensure `/v1/responses` behavior matches OpenAI Responses API expectations for request validation, streaming events, and error envelopes within upstream constraints.
## Requirements
### Requirement: Validate Responses create requests
The service MUST accept POST requests to `/v1/responses` with a JSON body and MUST validate required fields according to OpenAI Responses API expectations. The request MUST include `model` and `input`, MAY omit `instructions`, MUST reject mutually exclusive fields (`input` and `messages` when both are present), and MUST reject `store=true` with an OpenAI error envelope.

#### Scenario: Minimal valid request
- **WHEN** the client sends `{ "model": "gpt-4.1", "input": "hi" }`
- **THEN** the service accepts the request and begins a response (streaming or non-streaming based on `stream`)

#### Scenario: Invalid request fields
- **WHEN** the client omits `model` or `input`, or sends both `input` and `messages`
- **THEN** the service returns a 4xx response with an OpenAI error envelope describing the invalid parameter

### Requirement: Support Responses input types and conversation constraints
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST reject `previous_response_id` with an OpenAI error envelope because upstream does not support it. The service MUST continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: String input
- **WHEN** the client sends `input` as a string
- **THEN** the request is accepted and forwarded as a single `input_text` item

#### Scenario: Array input items
- **WHEN** the client sends `input` as an array of input items
- **THEN** the request is accepted and each item is forwarded in order

#### Scenario: conversation and previous_response_id conflict
- **WHEN** the client provides both `conversation` and `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating invalid parameters

#### Scenario: previous_response_id provided
- **WHEN** the client provides `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

### Requirement: Reject input_file file_id in Responses
The service MUST reject `input_file.file_id` in Responses input items and return a 4xx OpenAI invalid_request_error with message "Invalid request payload".

#### Scenario: input_file file_id rejected
- **WHEN** a request includes an input item with `{"type":"input_file","file_id":"file_123"}`
- **THEN** the service returns a 4xx OpenAI invalid_request_error with message "Invalid request payload" and param `input`

### Requirement: Stream Responses events with terminal completion
When `stream=true`, the service MUST respond with `text/event-stream` and emit OpenAI Responses streaming events. The stream MUST include a terminal event of `response.completed` or `response.failed`. If upstream closes the stream without a terminal event, the service MUST emit `response.failed` with a stable error code indicating an incomplete stream.

#### Scenario: Successful streaming completion
- **WHEN** the upstream emits `response.completed`
- **THEN** the service forwards the event and closes the stream

#### Scenario: Missing terminal event
- **WHEN** the upstream closes the stream without `response.completed` or `response.failed`
- **THEN** the service emits `response.failed` with an error code indicating an incomplete stream and closes the stream

### Requirement: Responses streaming event taxonomy
When streaming, the service MUST forward the standard Responses streaming event types, including `response.created`, `response.in_progress`, and `response.completed`/`response.failed` as applicable, preserving event order and `sequence_number` fields when present.

#### Scenario: response.created and response.in_progress present
- **WHEN** the upstream emits `response.created` followed by `response.in_progress`
- **THEN** the service forwards both events in order without mutation

### Requirement: Non-streaming Responses return a full response object
When `stream` is `false` or omitted, the service MUST return a JSON response object consistent with OpenAI Responses API, including `id`, `object: "response"`, `status`, `output`, and `usage` when available.

#### Scenario: Non-streaming response
- **WHEN** the client sends a valid request with `stream=false`
- **THEN** the service returns a single JSON response object containing output items and status

### Requirement: Reconstruct non-streaming Responses output from streamed item events
When serving non-streaming `/v1/responses`, the service MUST preserve output items emitted on upstream SSE item events even when the terminal `response.completed` or `response.incomplete` payload omits `response.output` or returns it as an empty list.

#### Scenario: Reasoning item emitted before terminal response
- **WHEN** upstream emits a reasoning or other output item on `response.output_item.done` and the terminal response omits `output`
- **THEN** the final non-streaming JSON response includes that output item in `output`

#### Scenario: Terminal response already includes output
- **WHEN** the terminal response already includes a non-empty `output` array
- **THEN** the service returns the terminal `output` array unchanged

### Requirement: Error envelope parity for invalid or unsupported requests
For invalid inputs or unsupported features, the service MUST return an OpenAI-style error envelope (`{ "error": { ... } }`) with stable `type`, `code`, and `param` fields. For streaming requests, errors MUST be emitted as `response.failed` events containing the same error envelope.

#### Scenario: Unsupported feature flag
- **WHEN** the client sets an unsupported feature (e.g., `store=true`)
- **THEN** the service returns an OpenAI error envelope (or `response.failed` for streaming) with a stable error code and message

### Requirement: Validate include values
If the client supplies `include`, the service MUST accept only values documented by the Responses API and MUST return a 4xx OpenAI error envelope for unknown include values.

#### Scenario: Known include value
- **WHEN** the client includes `message.output_text.logprobs`
- **THEN** the service accepts the request and includes logprobs in the response output when available

#### Scenario: Unknown include value
- **WHEN** the client includes an unsupported include value
- **THEN** the service returns a 4xx OpenAI error envelope indicating the invalid include entry

### Requirement: Allow web_search tools and reject unsupported built-ins
The service MUST accept Responses requests that include tools with type `web_search` or `web_search_preview`. The service MUST normalize `web_search_preview` to `web_search` before forwarding upstream. The service MUST reject other built-in tool types (file_search, code_interpreter, computer_use, computer_use_preview, image_generation) with an OpenAI invalid_request_error.

#### Scenario: web_search_preview tool accepted
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the service accepts the request and forwards the tool as `web_search`

#### Scenario: unsupported built-in tool rejected
- **WHEN** the client sends `tools=[{"type":"code_interpreter"}]`
- **THEN** the service returns a 4xx response with an OpenAI invalid_request_error indicating the unsupported tool type

### Requirement: Preserve supported service_tier values
When a Responses request includes `service_tier`, the service MUST preserve that field in the normalized upstream payload instead of dropping or rewriting it locally.

#### Scenario: Responses request includes fast-mode tier
- **WHEN** a client sends a valid Responses request with `service_tier: "priority"`
- **THEN** the service accepts the request and forwards `service_tier: "priority"` upstream unchanged

### Requirement: Inline input_image URLs when possible
When a request includes `input_image` parts with HTTP(S) URLs, the service MUST attempt to fetch the image and replace the URL with a data URL if the image is within size limits. If the image cannot be fetched or exceeds size limits, the service MUST preserve the original URL and allow upstream to handle the error.

#### Scenario: input_image URL fetched
- **WHEN** the request includes an HTTP(S) `input_image` URL that is reachable and within size limits
- **THEN** the service forwards the request with the image converted to a data URL

#### Scenario: input_image URL fetch fails
- **WHEN** the request includes an HTTP(S) `input_image` URL that cannot be fetched or exceeds limits
- **THEN** the service forwards the original URL unchanged

### Requirement: Reject truncation
The service MUST reject any request that includes `truncation`, returning an OpenAI error envelope indicating the unsupported parameter. The service MUST NOT forward `truncation` to upstream.

#### Scenario: truncation provided
- **WHEN** the client sends `truncation: "auto"` or `truncation: "disabled"`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

### Requirement: Usage mapping and propagation
When usage data is provided by the upstream, the service MUST include `input_tokens`, `output_tokens`, and `total_tokens` (and token detail fields if present) in `response.completed` events and in non-streaming responses.

#### Scenario: Usage included
- **WHEN** the upstream includes usage in `response.completed`
- **THEN** the service forwards usage fields in the completed event and in the final response object

### Requirement: Strip safety_identifier before upstream forwarding
Before forwarding Responses payloads upstream, the service MUST remove `safety_identifier` from normalized payloads for both standard and compact Responses endpoints.

#### Scenario: safety_identifier provided in Responses request
- **WHEN** a client sends a valid Responses request including `safety_identifier`
- **THEN** the service accepts the request and forwards payload without `safety_identifier`

#### Scenario: safety_identifier provided in Chat-mapped request
- **WHEN** a client sends a Chat Completions request including `safety_identifier`
- **THEN** the mapped Responses payload forwarded upstream excludes `safety_identifier`

### Requirement: Strip known unsupported advisory parameters before upstream forwarding
Before forwarding Responses payloads upstream, the service MUST remove known unsupported advisory parameters that upstream rejects with `unknown_parameter`. At minimum, the service MUST strip `prompt_cache_retention` and `temperature` from normalized payloads for both standard and compact Responses endpoints, and MUST preserve `prompt_cache_key`.

#### Scenario: prompt_cache_retention provided
- **WHEN** a client sends a valid Responses request that includes `prompt_cache_retention`
- **THEN** the service accepts the request and forwards payload without `prompt_cache_retention`

#### Scenario: temperature provided
- **WHEN** a client sends a valid Responses or Chat-mapped request that includes `temperature`
- **THEN** the service accepts the request and forwards payload without `temperature`

#### Scenario: unrelated extra field provided
- **WHEN** a client sends a valid request with an unrelated extra field not in the unsupported list
- **THEN** the service preserves that field in forwarded payload

### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as a bounded upstream account affinity key for prompt-cache correctness. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: recent /v1 responses request reuses prompt-cache affinity
- **WHEN** a client sends repeated `/v1/responses` requests with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **AND** the previous mapping is still within the configured freshness window
- **THEN** the service selects the same upstream account for those requests

#### Scenario: recent /v1 compact request reuses prompt-cache affinity
- **WHEN** a client sends `/v1/responses/compact` after `/v1/responses` with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **AND** the previous mapping is still within the configured freshness window
- **THEN** the compact request reuses the previously selected upstream account

#### Scenario: expired prompt-cache affinity rebalances
- **WHEN** a client sends a later OpenAI-style request with the same non-empty `prompt_cache_key`
- **AND** the stored mapping is older than the configured freshness window
- **THEN** the service ignores the stale mapping, re-runs account selection, and stores a fresh mapping for the chosen account

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

### Requirement: Normalize prompt cache aliases for upstream compatibility
Before forwarding Responses payloads upstream, the service MUST normalize OpenAI-compatible camelCase prompt cache controls so codex-lb applies compatibility behavior consistently. The service MUST forward `promptCacheKey` as `prompt_cache_key`, and MUST treat `promptCacheRetention` the same as `prompt_cache_retention` for stripping behavior.

#### Scenario: camelCase prompt cache fields provided
- **WHEN** a client sends `promptCacheKey` or `promptCacheRetention` on a valid Responses request
- **THEN** the service forwards `prompt_cache_key` with the same value and does not forward `prompt_cache_retention`

### Requirement: Sanitize unsupported interleaved and legacy chat input fields
Before forwarding Responses requests upstream, the service MUST remove unsupported interleaved reasoning and legacy chat fields from `input` items and content parts. The service MUST strip `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call` fields when they appear in `input` structures, and MUST remove unsupported reasoning-only content parts that are not accepted by upstream.

#### Scenario: Interleaved reasoning and legacy chat fields in input item
- **WHEN** a request includes an input item containing `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`
- **THEN** the service strips those fields before forwarding upstream

#### Scenario: Unsupported reasoning-only content part in input
- **WHEN** a request includes a content part that represents interleaved reasoning-only payload
- **THEN** the service removes that content part before forwarding upstream

### Requirement: Preserve supported top-level reasoning controls
When sanitizing interleaved reasoning input fields, the service MUST preserve supported top-level reasoning controls (`reasoning.effort`, `reasoning.summary`) and continue forwarding them unchanged.

#### Scenario: Top-level reasoning with interleaved input fields
- **WHEN** a request includes top-level `reasoning` plus interleaved reasoning fields inside `input`
- **THEN** top-level `reasoning` is preserved while unsupported `input` fields are removed

### Requirement: Normalize assistant text content part types for upstream compatibility
Before forwarding Responses requests upstream, the service MUST normalize assistant-role text content parts in `input` so they use `output_text` (not `input_text`) to satisfy upstream role-specific validation.

#### Scenario: Assistant input message uses input_text
- **WHEN** a request includes an `input` message with `role: "assistant"` and a text content part typed as `input_text`
- **THEN** the service rewrites that content part type to `output_text` before forwarding upstream

### Requirement: Normalize tool message history for upstream compatibility
Before forwarding Responses requests upstream, the service MUST normalize tool-role message history into Responses-native function call output items. Tool messages MUST include a non-empty call identifier and MUST be rewritten as `type: "function_call_output"` with the same call identifier.

#### Scenario: Tool message in conversation history
- **WHEN** a request includes a message with `role: "tool"`, `tool_call_id`, and text content
- **THEN** the service rewrites it to a `function_call_output` input item using `call_id` and tool output text before forwarding upstream

### Requirement: Reject unsupported message roles with client errors
When coercing v1 `messages` into Responses input, the service MUST reject messages that do not include a string role or use an unsupported role value.

#### Scenario: Unsupported message role
- **WHEN** a request includes a message role outside the supported set
- **THEN** the service returns a client-facing invalid payload error referencing `messages`

### Requirement: Strip proxy identity headers before upstream forwarding
Before forwarding requests to the upstream Responses endpoint, the service MUST strip network/proxy identity headers derived from downstream edges. The service MUST remove `Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `True-Client-IP`, and `CF-*` headers, and MUST continue to set upstream auth/account headers from internal account state.

#### Scenario: Request contains reverse-proxy forwarding headers
- **WHEN** the inbound request includes headers such as `X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded`, or `X-Real-IP`
- **THEN** those headers are not forwarded to upstream

#### Scenario: Request contains Cloudflare identity headers
- **WHEN** the inbound request includes headers such as `CF-Connecting-IP` or `CF-Ray`
- **THEN** those headers are not forwarded to upstream

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty `session_id` header, the service MUST use that value as the routing affinity key for upstream account selection. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled.

#### Scenario: Codex Responses request with session_id and sticky threads disabled
- **WHEN** `/backend-api/codex/responses` is called with a non-empty `session_id` header and `sticky_threads_enabled=false`
- **THEN** the selected upstream account is pinned to that `session_id` for later backend Codex requests on the same thread

#### Scenario: Compact request reuses pinned Codex session account
- **WHEN** `/backend-api/codex/responses/compact` is called with the same non-empty `session_id` header after routing preferences change
- **THEN** the service reuses the previously pinned upstream account for that thread instead of reallocating to a different account

#### Scenario: Compact retry uses refreshed provider account identity
- **WHEN** a pinned backend Codex compact request gets a `401` from upstream, refreshes the selected account, and retries
- **THEN** the retry forwards the refreshed account's `chatgpt-account-id` header instead of reusing the pre-refresh account header

### Requirement: Compact requests preserve upstream compaction semantics
The service MUST not impose a dedicated compact request total or read timeout for `/responses/compact` requests. To preserve provider-owned remote compaction semantics, the service MUST fulfill `/backend-api/codex/responses/compact` and `/v1/responses/compact` by calling the upstream ChatGPT Codex `/codex/responses/compact` endpoint directly and returning the upstream JSON payload as the canonical next context window without converting it into a standard buffered Responses result. The service MUST preserve provider-owned compact payload contents without pruning, reordering, or rewriting returned context items beyond generic JSON serialization. While using this direct compact transport, the service MUST preserve compact account-selection semantics, `session_id` affinity, `prompt_cache_key` affinity, `401` refresh-and-retry behavior, API key settlement, and HTTP request logging. The service MUST reject `store=true` as a client payload error, and it MUST omit `store` from the direct upstream compact request instead of forwarding `store=false`. If direct upstream compact execution fails before a valid compact JSON payload is accepted, the service MUST keep the request inside the compact contract. It MUST NOT silently substitute `/codex/responses`, reconstruct compact output from streamed Responses events, or synthesize a compact window locally. The service MAY perform a bounded retry only against `/codex/responses/compact` when the failure occurs in a provably safe transport phase before a valid compact JSON payload is accepted.

#### Scenario: Compact request returns raw upstream compaction payload
- **WHEN** a compact request succeeds and the upstream `/codex/responses/compact` response contains `object: "response.compaction"`
- **THEN** the service returns that JSON payload without rewriting it into `object: "response"`

#### Scenario: Compact request preserves provider-owned compaction summary
- **WHEN** the upstream compact response includes nested compaction fields such as `compaction_summary.encrypted_content`
- **THEN** the service returns those nested fields unchanged in the final JSON response

#### Scenario: Compact response includes retained items and encrypted compaction state
- **WHEN** the upstream compact response returns a window that includes retained context items plus provider-owned compaction state such as encrypted content
- **THEN** the service returns that window unchanged to the client

#### Scenario: Compact response object shape differs from normal Responses
- **WHEN** the upstream compact response uses a provider-owned compact object shape instead of a standard `object: "response"` payload
- **THEN** the service returns that compact object shape unchanged instead of coercing it into a normal Responses payload

#### Scenario: Direct compact request omits store
- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` without a `store` field
- **THEN** the direct upstream `/codex/responses/compact` request omits `store`

#### Scenario: Direct compact request sets store true
- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `store=true`
- **THEN** the service returns a 4xx OpenAI invalid payload error
- **AND** it does not forward any `store` field upstream

#### Scenario: Direct compact upstream returns an error envelope
- **WHEN** the upstream direct compact request returns a non-2xx OpenAI-format error payload
- **THEN** the service propagates the corresponding HTTP status and error envelope to the client

#### Scenario: Direct compact transport fails before response body is available
- **WHEN** the upstream `/codex/responses/compact` call times out, disconnects, or otherwise fails before yielding a valid compact JSON payload
- **THEN** the service may retry only `/codex/responses/compact` within a bounded retry budget
- **AND** it does not attempt a surrogate `/codex/responses` request

#### Scenario: Direct compact transport gets a safe retryable upstream failure
- **WHEN** the upstream `/codex/responses/compact` call fails with `401`, `502`, `503`, or `504` before a valid compact JSON payload is accepted
- **THEN** the service may retry only `/codex/responses/compact`
- **AND** it preserves the request's established compact routing and affinity semantics except for refreshed provider identity on `401`
- **AND** it does not call `/codex/responses`

#### Scenario: Direct compact response payload is invalid
- **WHEN** the upstream `/codex/responses/compact` call returns a non-error payload that is not valid compact JSON for pass-through
- **THEN** the service returns an upstream error to the client
- **AND** it does not retry via `/codex/responses`
- **AND** it does not synthesize or reconstruct a replacement compact window

#### Scenario: Compact request uses no timeout by default
- **WHEN** `/responses/compact` is called and no compact timeout override is configured
- **THEN** the service forwards the request without setting an upstream total or read timeout

### Requirement: Persist request log transport for Responses requests
The service MUST persist a stable `transport` value on `request_logs` for Responses proxy requests and MUST expose the same value through `/api/request-logs`. Requests accepted over HTTP on `/backend-api/codex/responses` or `/v1/responses` MUST persist `transport = "http"`. Requests accepted over WebSocket on those paths MUST persist `transport = "websocket"`.

#### Scenario: HTTP Responses request logs http transport
- **WHEN** a client completes a Responses request over HTTP on `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the persisted request log has `transport = "http"`
- **AND** `/api/request-logs` returns that row with `transport = "http"`

#### Scenario: WebSocket Responses request logs websocket transport
- **WHEN** a client completes a Responses request over WebSocket on `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the persisted request log has `transport = "websocket"`
- **AND** `/api/request-logs` returns that row with `transport = "websocket"`

### Requirement: Persist request kind and session correlation in request logs
The service MUST persist a stable `request_kind` value on `request_logs` for proxied request-log rows and MUST expose the same value through `/api/request-logs`. At minimum, standard Responses requests on `/backend-api/codex/responses`, `/v1/responses`, and their websocket equivalents MUST persist `request_kind = "responses"`, compact requests on `/backend-api/codex/responses/compact` and `/v1/responses/compact` MUST persist `request_kind = "compact"`, and transcription requests on `/backend-api/transcribe` or `/v1/audio/transcriptions` MUST persist `request_kind = "transcription"`.

When an inbound proxied request includes a non-empty `session_id` header, the service MUST persist a deterministic hashed `session_id_hash` value on the corresponding request-log row instead of storing the raw `session_id`. The same request-log API response MUST expose that hashed value for correlation. When no `session_id` header is present, the request-log row MAY keep `session_id_hash = null`.

#### Scenario: HTTP compact request log exposes compact kind and hashed session id
- **WHEN** a client completes `/backend-api/codex/responses/compact` with a non-empty `session_id` header
- **THEN** the persisted request log has `request_kind = "compact"`
- **AND** the persisted request log has a non-null `session_id_hash`
- **AND** `/api/request-logs` returns that row with `requestKind = "compact"` and the same `sessionIdHash`

#### Scenario: WebSocket Responses request log exposes responses kind and hashed session id
- **WHEN** a client completes a Responses request over WebSocket on `/backend-api/codex/responses` with a non-empty `session_id` header
- **THEN** the persisted request log has `request_kind = "responses"`
- **AND** the persisted request log has a non-null `session_id_hash`
- **AND** `/api/request-logs` returns that row with `requestKind = "responses"` and the same `sessionIdHash`

#### Scenario: Request without session header leaves session hash empty
- **WHEN** a proxied request completes without any inbound `session_id` header
- **THEN** the persisted request log keeps `session_id_hash = null`
- **AND** `/api/request-logs` returns that row with `sessionIdHash = null`

### Requirement: Emit opt-in safe service-tier trace logs
When service-tier trace logging is enabled, the service MUST emit a diagnostic log entry for Responses requests that records `request_id`, request `kind`, `requested_service_tier`, and upstream `actual_service_tier`. The diagnostic log MUST NOT include prompt text, input content, or the full request payload.

#### Scenario: Streaming request logs requested and actual service tiers
- **WHEN** a streaming Responses request is sent with `service_tier: "priority"` and the upstream stream reports `response.service_tier: "default"`
- **THEN** the service emits a diagnostic log entry containing `requested_service_tier=priority` and `actual_service_tier=default`

#### Scenario: Compact request keeps actual tier empty when upstream omits it
- **WHEN** a compact Responses request is sent with `service_tier: "priority"` and the upstream JSON response omits `service_tier`
- **THEN** the service emits a diagnostic log entry containing `requested_service_tier=priority` and `actual_service_tier=None`

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, and upstream stream attempts. The proxy MUST stop retrying once that budget is exhausted and MUST emit a stable `response.failed` event instead of waiting through repeated full upstream timeouts.

#### Scenario: Request budget expires before another attempt
- **WHEN** a streaming Responses request has consumed its configured request budget before the next retry attempt begins
- **THEN** the proxy emits `response.failed` with a stable timeout code
- **AND** the proxy does not start another upstream attempt

#### Scenario: Stalled stream fails within the shorter idle window
- **WHEN** the upstream opens a Responses stream but does not deliver events before the configured stream idle timeout elapses
- **THEN** the proxy emits `response.failed` for the stalled stream within that idle timeout
- **AND** the same client request does not consume multiple full idle windows retrying the same generic failure

### Requirement: Streaming Responses retries are limited to account-recoverable failures
The proxy MUST automatically retry streaming Responses requests only for failures that are recoverable by refreshing or rotating the selected account. The proxy MUST NOT automatically retry generic upstream failures such as stalled streams, upstream transport failures, or unspecified server errors.

#### Scenario: Account-specific rate limit triggers a retry
- **WHEN** the first upstream streaming event fails with an account-specific rate-limit or quota error that can be resolved by selecting another account
- **THEN** the proxy updates account state for that account
- **AND** the proxy may retry the request on another eligible account while budget remains

#### Scenario: Generic upstream failure does not trigger retry
- **WHEN** the first upstream streaming event fails with `stream_idle_timeout`, `upstream_unavailable`, or another generic upstream error
- **THEN** the proxy forwards that failure to the client
- **AND** the proxy does not automatically retry the same client request

### Requirement: Compact request-path latency is bounded without changing default CLI timeout parity
When `/responses/compact` performs account selection, token refresh, or upstream connection setup, the proxy MUST enforce a configurable request-path budget for those pre-response phases. The proxy MUST preserve the existing default compact behavior of not imposing an upstream read timeout unless an operator explicitly configures one.

#### Scenario: Compact request budget expires before upstream response handling begins
- **WHEN** a compact request exhausts its configured request-path budget during account selection, token refresh, or upstream connection setup
- **THEN** the proxy returns `502` with OpenAI-format error code `upstream_unavailable`
- **AND** it does not begin another retry attempt

#### Scenario: Default compact read path remains unbounded
- **WHEN** `/responses/compact` is called without an explicit compact read-timeout override
- **THEN** the proxy may still bound selection, refresh, and connect work
- **AND** it MUST NOT add a default upstream read timeout beyond the existing compact contract

### Requirement: Gated model selection failures expose stable proxy error codes
When account selection fails for an explicitly mapped gated model, the proxy MUST return a stable OpenAI-format error code that distinguishes plan support failures, stale additional-quota data, and zero eligible accounts. The canonical routed `quota_key` MUST drive those checks even if raw upstream `limit_name` aliases change.

#### Scenario: Missing fresh additional quota data returns a specific code
- **WHEN** a compact or streaming Responses request targets a mapped gated model and the latest persisted additional-usage snapshot for its canonical `quota_key` is unavailable or stale
- **THEN** the proxy returns an OpenAI-format error envelope with a stable code for unavailable additional quota data

#### Scenario: No eligible accounts returns a specific code
- **WHEN** a compact or streaming Responses request targets a mapped gated model and the canonical `quota_key` has fresh persisted data but no eligible accounts
- **THEN** the proxy returns an OpenAI-format error envelope with a stable code for zero eligible additional-quota accounts
