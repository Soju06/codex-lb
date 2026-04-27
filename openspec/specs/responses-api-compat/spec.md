# responses-api-compat Specification

## Purpose

Define Responses API compatibility contracts so Codex, OpenCode, and OpenAI-style clients preserve expected behavior.

## Requirements
### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as a bounded upstream account affinity key for prompt-cache correctness. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

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

### Requirement: Native `/backend-api/codex` routes accept the Codex tool surface
The service MUST accept native `/backend-api/codex/responses` HTTP and websocket requests that include the Codex Desktop tool surface. This includes custom tools plus built-in Codex tool types such as `image_generation`, `file_search`, `code_interpreter`, and `computer_use_preview`. The service MUST continue normalizing `web_search_preview` to `web_search` before forwarding upstream. OpenAI-style `/v1/*` routes MUST keep rejecting unsupported built-in tools with an invalid_request_error.

#### Scenario: backend responses accept native built-in and custom tools
- **WHEN** a client sends `/backend-api/codex/responses` with tools including `{"type":"custom","name":"exec"}` and `{"type":"image_generation"}`
- **THEN** the service accepts the request and forwards the tools upstream without returning an invalid_request_error

#### Scenario: v1 responses continue rejecting unsupported built-in tools
- **WHEN** a client sends `/v1/responses` with `tools=[{"type":"image_generation"}]`
- **THEN** the service returns a 4xx OpenAI invalid_request_error indicating the unsupported tool type

### Requirement: Bridge-enabled worker pools use addressable bridge owners

When the HTTP responses session bridge is enabled and the configured runtime worker count is greater than one, the service MUST NOT start a plain Uvicorn multi-worker process group with a shared bridge instance id. It MUST instead start a front listener plus single-worker backend processes where each backend has a unique bridge instance id and an advertised endpoint that can route owner handoff to that worker-local bridge session map.

#### Scenario: bridge-enabled multi-worker startup uses addressable workers

- **WHEN** the HTTP responses session bridge is enabled
- **AND** the configured worker count is greater than one
- **THEN** startup launches single-worker backend app processes with unique bridge instance ids
- **AND** each backend advertises a worker-specific bridge endpoint
- **AND** public HTTP and WebSocket traffic enters through one front listener

#### Scenario: bridge-disabled multi-worker startup remains plain Uvicorn

- **WHEN** the HTTP responses session bridge is disabled
- **AND** the configured worker count is greater than one
- **THEN** startup continues to pass the requested worker count directly to Uvicorn
