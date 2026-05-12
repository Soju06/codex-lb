# responses-api-compat Specification

## Purpose

Define Responses API compatibility contracts so Codex, OpenCode, and OpenAI-style clients preserve expected behavior.

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
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST accept `previous_response_id` when `conversation` is absent and MUST continue to reject requests that include both `conversation` and `previous_response_id`.

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
- **WHEN** the client provides `previous_response_id` without `conversation`
- **THEN** the service accepts the request and forwards `previous_response_id` upstream unchanged

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

### Requirement: Allow web_search and built-in Responses tools
The service MUST accept Responses requests that include tools with type `web_search` or `web_search_preview` and MUST normalize `web_search_preview` to `web_search` before forwarding upstream. For other built-in Responses tool types (including `file_search`, `code_interpreter`, `computer_use`, `computer_use_preview`, and `image_generation`), the service MUST accept the request and MUST forward the tool definitions to upstream unchanged except for the documented `web_search_preview` alias. The same behavior MUST apply on HTTP `/v1/responses`, HTTP `/backend-api/codex/responses`, and the WebSocket equivalents that carry `response.create` payloads. Chat Completions tool policy is out of scope for this requirement and remains governed by `chat-completions-compat`.

#### Scenario: web_search_preview tool accepted
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the service accepts the request and forwards the tool as `web_search`

#### Scenario: built-in Responses tool accepted over HTTP
- **WHEN** the client sends `/v1/responses` or `/backend-api/codex/responses` with a built-in tool such as `image_generation`, `file_search`, `code_interpreter`, `computer_use`, or `computer_use_preview`
- **THEN** the service accepts the request and forwards the tool definition unchanged except for the documented `web_search_preview` alias

#### Scenario: built-in Responses tool accepted over WebSocket
- **WHEN** the client sends a WebSocket `response.create` payload on `/v1/responses` or `/backend-api/codex/responses` with one or more built-in tools
- **THEN** the service accepts the request and forwards the tool definitions unchanged except for the documented `web_search_preview` alias

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

### Requirement: Public OpenAI-compatible route eligibility is provider-aware, transport-aware, and fallback-ordered
The service MUST treat upstream execution as a provider-aware decision instead of assuming every request targets the ChatGPT-web backend. `chatgpt_web` remains primary and `openai_platform` is fallback-only. Phase-1 Platform fallback covers HTTP `/v1/models`, stateless HTTP `/v1/responses`, stateless HTTP `/v1/responses/compact`, HTTP `/backend-api/codex/models`, stateless HTTP `/backend-api/codex/responses`, and stateless HTTP `/backend-api/codex/responses/compact` when the selected routing subject supports the requested route family, transport, model, and required features.

#### Scenario: Healthy ChatGPT-web remains primary for stateless public HTTP
- **WHEN** a request targets an eligible public HTTP route
- **AND** both `chatgpt_web` and `openai_platform` are configured for that route family
- **AND** at least one compatible ChatGPT-web candidate remains healthy under the configured primary and secondary drain thresholds
- **THEN** the request continues through the ChatGPT-web path
- **AND** the service does not switch to the Platform transport for that request

#### Scenario: HTTP `/v1/responses` falls back to an OpenAI Platform upstream after the ChatGPT pool is drained
- **WHEN** the deployment includes an `openai_platform` identity
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured primary and secondary drain thresholds
- **AND** the request does not require phase-1 unsupported continuity or websocket capabilities
- **THEN** the service forwards HTTP `/v1/responses` to the public upstream contract instead of the ChatGPT-private `/codex/responses` path

#### Scenario: HTTP `/v1/responses/compact` falls back to an OpenAI Platform compact upstream after the ChatGPT pool is drained
- **WHEN** the deployment includes an `openai_platform` identity
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested compact model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **THEN** the service forwards HTTP `/v1/responses/compact` through the Platform compact transport
- **AND** it does not rewrite the compact result into a normal Responses payload

#### Scenario: Backend Codex HTTP responses fall back to Platform after the ChatGPT pool is drained
- **WHEN** the deployment includes an `openai_platform` identity
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **AND** the request does not require websocket or payload-level continuity-dependent behavior
- **THEN** the service forwards HTTP `/backend-api/codex/responses` through the Platform transport instead of the ChatGPT-private upstream path

#### Scenario: Backend Codex HTTP compact responses fall back to Platform after the ChatGPT pool is drained
- **WHEN** the deployment includes an `openai_platform` identity
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available for the requested compact model
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **THEN** the service forwards HTTP `/backend-api/codex/responses/compact` through the Platform compact transport
- **AND** it preserves the compact result as the canonical next context window

#### Scenario: Backend Codex HTTP model discovery falls back to Platform after the ChatGPT pool is drained
- **WHEN** the deployment includes an `openai_platform` identity
- **AND** there is at least one active `chatgpt_web` account configured in the deployment
- **AND** a compatible Platform routing subject is available
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **THEN** the service may satisfy HTTP `/backend-api/codex/models` from Platform model discovery translated into the backend Codex response shape

#### Scenario: Platform identity is excluded from downstream websocket route selection in phase 1
- **WHEN** a request targets downstream websocket `/responses` or `/v1/responses`
- **AND** the candidate upstream routing subject is `openai_platform`
- **THEN** the service excludes that routing subject before transport start
- **AND** if no compatible `chatgpt_web` routing subject remains it returns a stable OpenAI-format error instead of attempting a ChatGPT-shaped websocket flow on behalf of Platform mode

#### Scenario: capability mismatch fails closed
- **WHEN** routing selects or is restricted to an upstream routing subject that does not support the requested route family, transport, or feature
- **THEN** the service rejects the request with a stable OpenAI-format error
- **AND** it MUST NOT silently substitute a different upstream contract to emulate unsupported behavior

#### Scenario: Public route rejects Platform-only fallback
- **WHEN** a request targets HTTP `/v1/models`, stateless HTTP `/v1/responses`, or stateless HTTP `/v1/responses/compact`
- **AND** an `openai_platform` identity is configured for that route family
- **AND** no eligible `chatgpt_web` routing subject exists for the requested model and route
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_fallback_requires_chatgpt"`

### Requirement: Continuity-dependent request shapes are gated before provider selection
The service MUST derive request capabilities from both route and request shape before it chooses an upstream routing subject. Requests are continuity-dependent when they rely on `conversation`, `previous_response_id`, explicit session headers, `x-codex-turn-state`, or downstream websocket continuity semantics. For HTTP `/backend-api/codex/responses`, downstream Codex session headers are transport hints and MUST NOT by themselves block Platform fallback in this increment; payload-level continuity fields remain unsupported.

#### Scenario: Platform-backed `conversation` request is rejected in phase 1
- **WHEN** a request targets HTTP `/v1/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `conversation`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param = "conversation"`

#### Scenario: Platform-backed `previous_response_id` request is rejected in phase 1
- **WHEN** a request targets HTTP `/v1/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `previous_response_id`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param = "previous_response_id"`

#### Scenario: Platform-backed session-affinity headers are rejected on public OpenAI-compatible routes in phase 1
- **WHEN** a request targets a public OpenAI-compatible route
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request carries `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and `param` set to the first offending continuity field name

#### Scenario: Backend Codex HTTP payload continuity request is rejected for Platform fallback
- **WHEN** a request targets HTTP `/backend-api/codex/responses`
- **AND** the allowed upstream candidates are restricted to `openai_platform`
- **AND** the request includes `conversation` or `previous_response_id`
- **THEN** the service rejects the request before upstream transport start with HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `code = "provider_continuity_unsupported"`

#### Scenario: Backend Codex HTTP session headers do not block Platform fallback
- **WHEN** a request targets HTTP `/backend-api/codex/responses`
- **AND** the allowed upstream candidates include an eligible `openai_platform` routing subject for `backend_codex_http`
- **AND** no compatible ChatGPT-web candidate remains healthy under the configured fallback thresholds
- **AND** the request includes `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`
- **AND** the request does not include `conversation` or `previous_response_id`
- **THEN** the service MAY route the request to Platform fallback
- **AND** those downstream session headers MUST NOT by themselves trigger `provider_continuity_unsupported`

### Requirement: Platform mode rejects phase-1 unsupported routes and features
When the selected upstream provider is `openai_platform`, the service MUST explicitly reject routes and features that still depend on ChatGPT-private or phase-gated contracts until equivalent public semantics are intentionally implemented and verified.

#### Scenario: Platform-backed compact request stays inside the compact contract
- **WHEN** an `openai_platform` routing subject receives `/v1/responses/compact` or `/backend-api/codex/responses/compact`
- **THEN** the service keeps the request on the provider-native compact transport
- **AND** it MUST NOT substitute a standard `/responses` request or synthesize compact output locally

#### Scenario: Backend Codex websocket remains unsupported for Platform fallback
- **WHEN** an `openai_platform` routing subject receives `/backend-api/codex/responses` over websocket transport
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `code = "provider_transport_unsupported"`

### Requirement: Provider mismatch errors use stable codes
For provider-specific capability failures introduced by provider-aware public-route fallback, the service MUST use stable OpenAI-style error envelopes and stable proxy-defined codes so tests and clients can distinguish route, transport, and continuity failures.

#### Scenario: transport mismatch returns a stable code
- **WHEN** a request is rejected because the selected provider does not support the requested downstream transport
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_transport_unsupported"`, and `param = "transport"`

#### Scenario: continuity mismatch returns a stable code
- **WHEN** a request is rejected because the selected provider does not support the required continuity behavior
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"`, `code = "provider_continuity_unsupported"`, and a `param` pointing to the offending continuity field when one exists

#### Scenario: route or feature mismatch returns a stable code
- **WHEN** a request is rejected because the selected provider does not support the requested route family or feature such as compact or backend Codex
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_feature_unsupported"`

#### Scenario: fallback prerequisite returns a stable code
- **WHEN** a request is rejected because `openai_platform` is configured but no eligible `chatgpt_web` routing subject exists for fallback
- **THEN** the service returns HTTP `400`
- **AND** it returns an OpenAI-format error envelope with `type = "invalid_request_error"` and `code = "provider_fallback_requires_chatgpt"`
### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as a bounded upstream target affinity key for prompt-cache correctness. When the selected upstream provider is `chatgpt_web`, this continues to mean bounded upstream account affinity. When the selected upstream provider is `openai_platform`, it MUST preserve affinity to the selected provider-scoped routing target without implying ChatGPT-specific session continuity or widening the request's capability set. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: recent /v1 responses request reuses prompt-cache affinity
- **WHEN** a client sends repeated `/v1/responses` requests with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **AND** the previous mapping is still within the configured freshness window
- **THEN** the service selects the same upstream routing target for those requests

#### Scenario: recent /v1 compact request reuses prompt-cache affinity
- **WHEN** a client sends `/v1/responses/compact` after `/v1/responses` with the same non-empty `prompt_cache_key` while `sticky_threads_enabled` is disabled
- **AND** the previous mapping is still within the configured freshness window
- **THEN** the compact request reuses the previously selected upstream routing target

#### Scenario: expired prompt-cache affinity rebalances
- **WHEN** a client sends a later OpenAI-style request with the same non-empty `prompt_cache_key`
- **AND** the stored mapping is older than the configured freshness window
- **THEN** the service ignores the stale mapping, re-runs account selection, and stores a fresh mapping for the chosen account

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

#### Scenario: Platform prompt-cache affinity reuses the same provider target
- **WHEN** a client sends repeated stateless HTTP `/v1/responses` requests with the same non-empty `prompt_cache_key`
- **AND** the selected upstream provider is `openai_platform`
- **AND** the existing mapping is still within the configured freshness window
- **THEN** the service reuses the same provider-scoped routing target for those requests

#### Scenario: Prompt-cache affinity does not suppress a drained public fallback decision
- **WHEN** a public stateless HTTP request carries a bounded `prompt_cache_key` affinity
- **AND** no compatible ChatGPT-web candidate remains selectable and above the configured fallback thresholds
- **THEN** the service MAY route the request to `openai_platform`
- **AND** prompt-cache affinity alone MUST NOT keep the request on ChatGPT

### Requirement: HTTP Responses routes preserve upstream continuity only for providers that advertise it
When the selected upstream provider exposes durable upstream continuity for HTTP Responses routes, the service MUST preserve that continuity on a stable bridge key. When the selected upstream provider does not expose equivalent continuity semantics for the requested route family, the service MUST NOT synthesize ChatGPT-style continuity or silently open a different provider contract to satisfy the request.

#### Scenario: sequential HTTP /v1/responses requests reuse the same bridged upstream session
- **WHEN** a client sends repeated HTTP `/v1/responses` requests with the same stable bridge key
- **THEN** the service reuses one upstream websocket session for those requests instead of opening a fresh upstream session per request

#### Scenario: sequential backend Codex HTTP requests reuse the same bridged upstream session
- **WHEN** a client sends repeated HTTP `/backend-api/codex/responses` requests with the same stable bridge key
- **THEN** the service reuses one upstream websocket session for those requests instead of opening a fresh upstream session per request

#### Scenario: HTTP previous_response_id remains valid within a bridged /v1 session
- **WHEN** a client sends a later HTTP `/v1/responses` request with `previous_response_id` that references a response created earlier on the same bridged session
- **THEN** the service forwards that request through the same upstream websocket session so upstream can resolve the referenced prior response

#### Scenario: backend Codex HTTP previous_response_id remains valid within a bridged session
- **WHEN** a client sends a later HTTP `/backend-api/codex/responses` request with `previous_response_id` that references a response created earlier on the same bridged session
- **THEN** the service forwards that request through the same upstream websocket session so upstream can resolve the referenced prior response

#### Scenario: HTTP previous_response_id fails closed when bridged continuity is unavailable
- **WHEN** a client sends HTTP `/v1/responses` or `/backend-api/codex/responses` with `previous_response_id`
- **AND** there is no matching live bridged upstream websocket session for that continuity key
- **THEN** the service MUST fail the request without opening a fresh upstream session
- **AND** it MUST return `previous_response_not_found` on `previous_response_id`

#### Scenario: bridged HTTP requests keep external HTTP transport logging
- **WHEN** the service fulfills an HTTP `/v1/responses` or `/backend-api/codex/responses` request through an internal upstream websocket bridge
- **THEN** the persisted request log still records `transport = "http"`

#### Scenario: clean upstream close forces a fresh bridged session
- **WHEN** an existing bridged upstream websocket closes cleanly after prior HTTP `/v1/responses` or `/backend-api/codex/responses` work completes
- **THEN** the next compatible HTTP request for that same bridge key opens a fresh upstream websocket session instead of reusing the closed session

#### Scenario: active bridge pool exhaustion fails fast without evicting live sessions
- **WHEN** the HTTP Responses bridge pool has reached its configured maximum session count
- **AND** every existing bridge session still has pending in-flight requests
- **THEN** the service MUST NOT evict those active bridge sessions
- **AND** it MUST fail the new request fast with `429 rate_limit_exceeded`

#### Scenario: HTTP responses route emits a turn-state header for later continuity
- **WHEN** a client sends an HTTP `/v1/responses` or `/backend-api/codex/responses` request without `x-codex-turn-state`
- **THEN** the service returns an `x-codex-turn-state` response header
- **AND** replaying that header on a later compatible request upgrades the bridge key to Codex-session affinity

#### Scenario: codex-session bridge sessions outlive prompt-cache sessions
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **THEN** the service applies the longer Codex bridge idle TTL instead of the generic prompt-cache TTL
- **AND** when idle eviction is required the service prefers evicting non-Codex prompt-cache bridge sessions before idle Codex-affinity bridge sessions

#### Scenario: optional Codex-affinity bridge prewarm stays behind an explicit flag
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **AND** Codex bridge prewarm is disabled
- **THEN** the first client-visible request is sent upstream directly without an extra internal warmup request

#### Scenario: enabled Codex-affinity bridge prewarm preserves the HTTP contract
- **WHEN** an HTTP `/v1/responses` bridge session is keyed by Codex turn/session affinity
- **AND** Codex bridge prewarm is enabled
- **AND** the first request on that session does not already reference `previous_response_id`
- **THEN** the service sends one internal `response.create` prewarm with `generate=false` before the client-visible request
- **AND** the client-visible response contract remains unchanged

#### Scenario: bridge enforces deterministic owner instance for hard continuity keys
- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state` or an explicit session header
- **AND** that request lands on a non-owner instance
- **THEN** the service fails the request fast with `bridge_instance_mismatch`
- **AND** it MUST NOT create a fresh local bridge session for that key on the wrong instance

#### Scenario: gateway-safe prompt-cache bridge requests tolerate wrong-replica arrival
- **WHEN** operators enable HTTP bridge gateway-safe mode
- **AND** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST NOT return `bridge_instance_mismatch` solely because prompt-cache locality was missed

#### Scenario: ChatGPT-web continuity remains unchanged
- **WHEN** the selected upstream provider is `chatgpt_web`
- **THEN** existing HTTP bridge reuse and `previous_response_id` continuity guarantees continue to apply

#### Scenario: Platform continuity-dependent request fails closed when parity is unavailable
- **WHEN** the selected upstream provider is `openai_platform`
- **AND** a request depends on provider-owned continuity semantics that are not implemented for that provider in phase 1
- **THEN** the service rejects the request with code `provider_continuity_unsupported`
- **AND** it does so before upstream transport start
- **AND** it MUST NOT create a fake ChatGPT-style bridge session on the client's behalf

#### Scenario: Platform-only public-route operation is not allowed
- **WHEN** a request targets an eligible public HTTP route
- **AND** an `openai_platform` identity exists
- **AND** there is no compatible `chatgpt_web` pool available for the deployment
- **THEN** the service MUST NOT execute the request through Platform alone
- **AND** it fails closed with code `provider_fallback_requires_chatgpt`

### Requirement: Websocket responses advertise and honor Codex turn-state affinity
When serving websocket Responses endpoints, the service MUST advertise an `x-codex-turn-state` header during websocket accept. If the client reconnects and presents that same `x-codex-turn-state`, the service MUST treat it as the highest-priority Codex-affinity key for upstream routing on that websocket turn. On `/v1/responses`, a proxy-generated turn-state MUST NOT override the first request's prompt-cache routing unless the client explicitly sends the turn-state back.

#### Scenario: backend websocket generates a turn-state for native Codex clients
- **WHEN** a client opens `/backend-api/codex/responses` without an existing `x-codex-turn-state`
- **THEN** the websocket accept response includes a generated non-empty `x-codex-turn-state`
- **AND** the proxy uses that same generated turn-state as the Codex session affinity key for the upstream websocket

#### Scenario: websocket reconnect honors client-provided turn-state
- **WHEN** a client opens a websocket Responses route and provides `x-codex-turn-state`
- **THEN** the websocket accept response echoes that same turn-state
- **AND** the proxy uses that same turn-state as the Codex session affinity key

### Requirement: Auto websocket fallback remains narrow and explicit
When automatic upstream transport selection prefers websocket, the service MUST only downgrade to HTTP automatically on `426 Upgrade Required`. Handshake failures such as `403 Forbidden` or `404 Not Found` MUST surface as upstream errors instead of silently falling back to HTTP.

#### Scenario: forbidden websocket handshake does not silently downgrade
- **WHEN** auto transport chooses websocket and upstream rejects the websocket handshake with `403`
- **THEN** the service returns an upstream error
- **AND** it MUST NOT retry the same request over HTTP automatically

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
The service MUST preserve the selected provider's native compact contract for `/backend-api/codex/responses/compact` and `/v1/responses/compact`. To preserve provider-owned remote compaction semantics, the service MUST fulfill compact requests by calling the selected provider's native compact endpoint directly and returning the upstream JSON payload as the canonical next context window without converting it into a standard buffered Responses result. The service MUST preserve provider-owned compact payload contents without pruning, reordering, or rewriting returned context items beyond generic JSON serialization. While using this direct compact transport, the service MUST preserve compact account-selection semantics, `session_id` affinity, `prompt_cache_key` affinity, bounded same-contract retries, API key settlement, and HTTP request logging. The service MUST reject `store=true` as a client payload error, and it MUST omit `store` from the direct upstream compact request instead of forwarding `store=false`. If direct upstream compact execution fails before a valid compact JSON payload is accepted, the service MUST keep the request inside the selected provider's compact contract. It MUST NOT silently substitute a standard `/responses` request, reconstruct compact output from streamed Responses events, or synthesize a compact window locally. The service MAY apply provider-specific transport timeouts and bounded retries only against the selected provider's compact endpoint when the failure occurs in a provably safe transport phase before a valid compact JSON payload is accepted.

#### Scenario: Compact request returns raw upstream compaction payload
- **WHEN** a compact request succeeds and the selected provider's compact endpoint returns `object: "response.compaction"`
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
- **THEN** the selected provider-native compact request omits `store`

#### Scenario: Direct compact request sets store true
- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `store=true`
- **THEN** the service returns a 4xx OpenAI invalid payload error
- **AND** it does not forward any `store` field upstream

#### Scenario: Direct compact upstream returns an error envelope
- **WHEN** the selected provider-native compact request returns a non-2xx OpenAI-format error payload
- **THEN** the service propagates the corresponding HTTP status and error envelope to the client

#### Scenario: Backend Codex compact falls back to public Platform compact transport
- **WHEN** a client sends `/backend-api/codex/responses/compact`
- **AND** the selected upstream provider is `openai_platform`
- **THEN** the service translates the request onto the public Platform compact contract
- **AND** it still returns the resulting compact payload unchanged to the backend Codex client

#### Scenario: Direct compact transport fails before response body is available
- **WHEN** the selected provider's compact call times out, disconnects, or otherwise fails before yielding a valid compact JSON payload
- **THEN** the service may retry only that provider's compact endpoint within a bounded retry budget
- **AND** it does not attempt a surrogate standard `/responses` request

#### Scenario: Direct compact transport gets a safe retryable upstream failure
- **WHEN** the selected provider's compact call fails with `401`, `502`, `503`, or `504` before a valid compact JSON payload is accepted
- **THEN** the service may retry only that provider's compact endpoint
- **AND** it preserves the request's established compact routing and affinity semantics except for refreshed provider identity on `401`
- **AND** it does not call a standard `/responses` endpoint

#### Scenario: Direct compact response payload is invalid
- **WHEN** the selected provider's compact call returns a non-error payload that is not valid compact JSON for pass-through
- **THEN** the service returns an upstream error to the client
- **AND** it does not retry via a standard `/responses` endpoint
- **AND** it does not synthesize or reconstruct a replacement compact window

#### Scenario: ChatGPT compact request uses no timeout by default
- **WHEN** `/responses/compact` is routed to the `chatgpt_web` compact endpoint
- **AND** no compact timeout override is configured
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
### Requirement: Responses requests reject uploaded input_image references
The system SHALL accept `{"type":"input_file","file_id":"file_*"}` attached-file items in `/v1/responses`, `/backend-api/codex/responses`, and `/responses/compact` request payloads and forward them verbatim.

When an `input_image` part contains a `file_id` field or an `image_url` starting with `sediment://`, the proxy MUST return HTTP 400 with `error.code = "unsupported_input_image_format"` and an explanation that the upstream Responses API only accepts inline `data:` URLs for `input_image`. The proxy MUST NOT fetch the upload, MUST NOT inline-convert the image, and MUST NOT trim, slim, or rewrite any conversation content.

#### Scenario: input_image file_id is rejected before forwarding
- **WHEN** a `/v1/responses` request contains `{"type":"input_image","file_id":"file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** the response explains that inline `data:` URLs are the supported `input_image` contract

#### Scenario: sediment upload URL is rejected before forwarding
- **WHEN** a `/responses/compact` request contains `{"type":"input_image","image_url":"sediment://file_img"}`
- **THEN** the proxy returns HTTP 400 with `error.code = "unsupported_input_image_format"`
- **AND** does not fetch or inline-convert the upload

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
