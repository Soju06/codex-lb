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
Before forwarding Responses payloads upstream, the service MUST remove known unsupported advisory parameters that upstream rejects with `unknown_parameter`. At minimum, the service MUST strip `prompt_cache_retention` and `temperature` from normalized payloads for both standard and compact Responses endpoints.

#### Scenario: prompt_cache_retention provided
- **WHEN** a client sends a valid Responses request that includes `prompt_cache_retention`
- **THEN** the service accepts the request and forwards payload without `prompt_cache_retention`

#### Scenario: temperature provided
- **WHEN** a client sends a valid Responses or Chat-mapped request that includes `temperature`
- **THEN** the service accepts the request and forwards payload without `temperature`

#### Scenario: unrelated extra field provided
- **WHEN** a client sends a valid request with an unrelated extra field not in the unsupported list
- **THEN** the service preserves that field in forwarded payload

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

### Requirement: Compact upstream timeout matches Codex CLI by default
The service MUST not impose a compact request timeout by default for `/responses/compact` requests. The service MAY allow operators to configure an explicit compact timeout override, and upstream read timeouts on compact MUST surface as `502` OpenAI-format errors with code `upstream_unavailable`.

#### Scenario: Compact request exceeds read timeout
- **WHEN** an upstream compact request does not produce a complete JSON response before the configured compact timeout elapses
- **THEN** the service returns `502` with error code `upstream_unavailable`

#### Scenario: Compact request uses no timeout by default
- **WHEN** `/responses/compact` is called and no compact timeout override is configured
- **THEN** the service forwards the request without setting an upstream total or read timeout
