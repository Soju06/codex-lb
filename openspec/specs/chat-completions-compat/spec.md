# Chat Completions Compatibility

## Purpose

Ensure `/v1/chat/completions` behavior matches OpenAI Chat Completions expectations by mapping to Responses semantics and preserving streaming and error envelopes.
## Requirements
### Requirement: Validate Chat Completions requests

The service MUST accept POST requests to `/v1/chat/completions` with a JSON body and MUST validate required fields according to OpenAI
Chat Completions expectations. The request MUST include `model` and either a non-empty `messages` array of objects or a Responses-shaped `input`
payload. Invalid payloads MUST return a 4xx response with an OpenAI error envelope.

#### Scenario: Responses-shaped chat payload accepted

- **WHEN** the client sends `{ "model": "gpt-5.2", "input": [{"role":"user","content":[{"type":"input_text","text":"hi"}]}] }`
- **THEN** the service accepts the request and forwards it as a Responses payload without requiring `messages`

#### Scenario: Invalid messages payload

- **WHEN** the client sends an empty `messages` array without `input`, or sends non-object message items
- **THEN** the service returns a 4xx response with an OpenAI error envelope describing the invalid parameter

#### Scenario: Minimal valid chat request
- **WHEN** the client sends `{ "model": "gpt-4.1", "messages": [{"role":"user","content":"hi"}] }`
- **THEN** the service accepts the request and begins a response (streaming or non-streaming based on `stream`)

### Requirement: Enforce message content type rules
The service MUST enforce role-specific message content rules: `system` and `developer` messages MUST contain text-only content, while `user` messages MAY contain text, image, or file content parts per OpenAI chat spec. Unsupported content types MUST return an OpenAI error envelope.

#### Scenario: Non-text system message
- **WHEN** a `system` or `developer` message includes a non-text content part
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating an invalid message content type

#### Scenario: User message with mixed content
- **WHEN** a `user` message includes a mix of text and image parts
- **THEN** the service accepts the request and forwards the content parts in order

### Requirement: Map chat requests to Responses wire format

The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. When `response_format.type` is `json_object`, the service MUST instead preserve `system`/`developer` messages as Responses input role messages so JSON-mode instructions remain in the request context. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, and `response_format` MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **AND** the request does not use `response_format.type = "json_object"`
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: JSON object response format preserves instruction-role messages
- **WHEN** the client sends `response_format: {"type":"json_object"}` with a `system` or `developer` message that instructs JSON output
- **THEN** the mapped Responses payload keeps that message in `input` with its original role
- **AND** the mapped `instructions` value does not contain that message content

#### Scenario: Tool choice values
- **WHEN** the client sets `tool_choice` to `none`, `auto`, or `required`
- **THEN** the service forwards the value consistently in the mapped Responses request

### Requirement: Chat Completions omit unset tools on the mapped Responses payload

When a `/v1/chat/completions` request omits the top-level `tools` field, the mapped Responses request MUST leave `tools` unset and the forwarded upstream payload MUST NOT include `tools`. An explicit client-sent empty `tools` array MUST still be forwarded as `[]`.

#### Scenario: Omitted chat tools stay omitted upstream

- **GIVEN** a Chat Completions request with `messages` and no `tools` field
- **WHEN** the service maps the request to Responses and forwards it
- **THEN** `tools` is absent from the mapped request field set
- **AND** the upstream payload does not include `tools`

#### Scenario: Explicit empty chat tools stay explicit

- **GIVEN** a Chat Completions request that sends `"tools": []`
- **WHEN** the service maps the request to Responses
- **THEN** the mapped payload includes `"tools": []`

### Requirement: Preserve service_tier in Chat Completions mapping
When a Chat Completions request includes `service_tier`, the service MUST preserve that field when mapping the request to the internal Responses payload.

#### Scenario: Chat request includes fast-mode tier
- **WHEN** a client sends a valid Chat Completions request with `service_tier: "priority"`
- **THEN** the mapped Responses payload forwarded upstream includes `service_tier: "priority"`

### Requirement: Allow web_search tools in Chat Completions

The service MUST accept Chat Completions requests that include tools with type `web_search` or `web_search_preview`. The service MUST normalize
`web_search_preview` to `web_search` when mapping to the Responses tool schema. For requests with a non-empty `messages` array, the service MUST
continue to reject other built-in tool types (file_search, code_interpreter, computer_use, computer_use_preview, image_generation) with an OpenAI
invalid_request_error. For Responses-shaped chat payloads using `input` with `messages` absent or empty, the service MUST preserve Responses tool
definitions and `tool_choice`, including built-in Responses tools accepted by `/v1/responses`.

#### Scenario: unsupported built-in tool rejected for chat messages

- **WHEN** the client sends `tools=[{"type":"image_generation"}]`
- **AND** the request includes a non-empty `messages` array
- **THEN** the service returns a 4xx OpenAI invalid_request_error indicating the unsupported tool type

#### Scenario: Responses-shaped built-in tool preserved

- **WHEN** the client sends a `/v1/chat/completions` payload with `input`, `messages` absent or empty, `tools=[{"type":"image_generation"}]`, and `tool_choice={"type":"image_generation"}`
- **THEN** the mapped Responses request preserves the `image_generation` tool and `tool_choice`

#### Scenario: web_search_preview tool normalized in mapping
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the mapped Responses request includes a tool with type `web_search`

### Requirement: Reject file_id in Chat Completions
The service MUST reject chat `file` content parts that include `file_id` and return a 4xx OpenAI invalid_request_error with message "Invalid request payload".

#### Scenario: file_id rejected in chat file part
- **WHEN** a user message includes `{ "type": "file", "file": {"file_id":"file_123"} }`
- **THEN** the service returns a 4xx OpenAI invalid_request_error with message "Invalid request payload" and param `messages`

### Requirement: Streaming chat completions are emitted as chat.completion.chunk
When `stream=true`, the service MUST respond with `text/event-stream` and emit `chat.completion.chunk` payloads. The first chunk MUST include the `assistant` role, tool call deltas MUST be streamed when present, and the stream MUST terminate with `data: [DONE]`.

#### Scenario: Streaming content and termination
- **WHEN** the upstream emits text deltas and completes
- **THEN** the service emits `chat.completion.chunk` deltas with an initial role and ends with `data: [DONE]`

#### Scenario: Stream usage chunk
- **WHEN** the client sets `stream_options: { "include_usage": true }`
- **THEN** the stream includes a final chunk with a `usage` field and empty `choices` before `data: [DONE]`

### Requirement: Non-streaming chat completions return a full chat.completion object
When `stream` is `false` or omitted, the service MUST return a single `chat.completion` JSON object containing `id`, `model`, `choices`, and `usage` when available. If tool calls are present, the message MUST include `tool_calls` and the `finish_reason` MUST be `tool_calls`.

#### Scenario: Non-streaming tool call response
- **WHEN** the upstream indicates a tool call sequence
- **THEN** the returned `chat.completion` includes `tool_calls` and a `finish_reason` of `tool_calls`

### Requirement: Non-streaming chat collect closes the upstream generator

When `stream` is `false` or omitted, `POST /v1/chat/completions` MUST close the upstream Responses generator after collect returns or raises, including when the first consumed event is `response.failed` or `error`. Closing MUST run the generator finalizer so an open API-key reservation is released or settled before the HTTP response is returned.

#### Scenario: First-event rate limit releases the reservation

- **WHEN** the startup probe did not consume the stream
- **AND** the first upstream event is `response.failed` with
  `code=rate_limit_exceeded`
- **AND** the request reserved API-key usage
- **THEN** the reservation is released before the error response is returned

### Requirement: Non-streaming chat errors use the Responses status map

When non-streaming `POST /v1/chat/completions` returns an OpenAI error envelope collected from the upstream Responses stream, the HTTP status MUST match the non-streaming `/v1/responses` mapping for that envelope (`429` for `rate_limit_exceeded`, `503` for unavailable-selection codes, `401`/`400` where that path already maps them). The envelope body MUST remain an OpenAI error object.

#### Scenario: Collected rate limit is 429

- **WHEN** non-streaming chat collect returns
  `{ "error": { "code": "rate_limit_exceeded", ... } }`
- **THEN** the HTTP status is `429`
- **AND** the body is that OpenAI error envelope

### Requirement: response_format mapping
If the client sends `response_format`, the service MUST translate it to the Responses `text.format` controls. For `json_schema`, the schema payload MUST be validated and missing `json_schema` MUST result in a 4xx error with an OpenAI error envelope.

#### Scenario: JSON schema response format
- **WHEN** the client sends `response_format: {"type":"json_schema","json_schema":{...}}`
- **THEN** the service maps to `text.format` and preserves schema fields

#### Scenario: Missing json_schema
- **WHEN** the client sends `response_format` with `type: "json_schema"` but omits `json_schema`
- **THEN** the service returns a 4xx response with an OpenAI error envelope

#### Scenario: Invalid json_schema name
- **WHEN** the client provides a `json_schema.name` outside the allowed pattern or length
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the invalid name

### Requirement: Large image inputs are handled per OpenAI limits
If a `user` message includes an image input larger than 8MB, the service MUST drop the image input from the request in accordance with OpenAI chat input limits.

#### Scenario: Oversized image input
- **WHEN** a `user` message includes an image input larger than 8MB
- **THEN** the service drops the image input and proceeds with remaining parts

### Requirement: Reject input_audio in Chat Completions
The service MUST reject chat user content parts with type `input_audio` and return a 4xx OpenAI invalid_request_error.

#### Scenario: input_audio rejected
- **WHEN** a user message includes `{ "type": "input_audio", "input_audio": {"data":"...","format":"wav"} }`
- **THEN** the service returns a 4xx OpenAI invalid_request_error indicating audio input is unsupported

### Requirement: Error mapping for chat requests
For upstream failures or invalid requests, the service MUST return an OpenAI error envelope for non-streaming responses and MUST emit an error chunk followed by `data: [DONE]` for streaming responses. Error `code`, `type`, and `message` MUST be preserved or normalized into stable values.

#### Scenario: Streaming error
- **WHEN** the upstream returns a failure during streaming
- **THEN** the service emits an error chunk and terminates the stream with `data: [DONE]`

#### Scenario: Non-streaming collected rate limit
- **WHEN** non-streaming collect returns `rate_limit_exceeded`
- **THEN** the service returns that OpenAI error envelope with HTTP 429

### Requirement: Drop unknown message-object fields during coercion

The service MUST drop unknown keys on a chat message object when coercing the message into a Responses API input message item. Specifically, when a chat message is converted into an `input` message item (role `user` / `assistant` without tool_calls, or the message-content half of an assistant message that also has tool_calls), the emitted item MUST contain exactly the keys `role` and `content`. Other fields on the inbound chat message — the documented but unsupported `name` field, any other standard chat-message field that has no Responses input-item equivalent, and any arbitrary client-supplied key (including keys starting with `_`) — MUST NOT appear on the emitted item.

This matches OpenAI's own `/v1/chat/completions`, which parses the known chat-message fields and silently ignores everything else rather than forwarding it. The Responses API input message item only accepts `role` + `content`; forwarding any other key triggers an upstream `unknown_parameter` rejection.

The Requirement applies only to message-item shapes. The existing tool-call decomposition (`FunctionCallInputItem`) and tool-message conversion (`FunctionCallOutputInputItem`) paths already select fields explicitly and are unaffected.

#### Scenario: Standard `name` field is dropped

- **WHEN** a client sends `{ "model": "...", "messages": [{ "role": "user", "content": "hi", "name": "alice" }] }`
- **THEN** the mapped Responses payload's `input` contains exactly `[{ "role": "user", "content": [{"type": "input_text", "text": "hi"}] }]` with no `name` key on the input item

#### Scenario: Arbitrary client-internal keys are dropped

- **WHEN** a client sends a message carrying client-internal bookkeeping keys, e.g. `{ "role": "user", "content": "hi", "_client_marker": true, "extra": 1 }`
- **THEN** the mapped Responses payload's `input` item for that message contains exactly `role` + `content` and no other keys

#### Scenario: Assistant message with tool_calls drops unknown keys on the message half

- **WHEN** a client sends an assistant message that has both `content` and `tool_calls`, with extra keys on the message object (`name`, `_client_marker`, etc.)
- **THEN** the message-content half of the decomposed input items is `{ "role": "assistant", "content": [...] }` with no `name` / `_client_marker` keys, and the tool-call half remains a well-formed `function_call` input item

#### Scenario: No regression for clean messages

- **WHEN** a client sends a message that already carries only `role` and `content` (the most common case)
- **THEN** the mapped Responses payload's `input` item for that message is byte-equivalent to the previous behavior

### Requirement: `_normalize_chat_tools` preserves the function tool strict flag

The chat → responses coercion pipeline MUST preserve the `strict` field on `function` tools when normalizing the chat-completions `tools[]` array into the Responses-API tool item shape. Specifically, when a chat tool of shape `{ "type": "function", "function": { "name": "...", "parameters": {...}, "strict": <bool> } }` is normalized, the emitted Responses tool item MUST set `"strict": <bool>` (mirroring the inbound value) and not silently drop it.

This is required because the chat-completions endpoint enters `enforce_strict_function_tools_format` via `to_responses_request()` after coercion. Dropping the strict flag during normalization would (a) mask spec-violating tool schemas, contradicting the strict-mode pre-validation requirement on the responses-api-compat capability, and (b) leave `/v1/chat/completions` and `/v1/responses` with divergent behavior for the same logical payload.

For non-function tool types (`web_search`, etc.), the strict flag is not applicable and behavior is unchanged.

#### Scenario: Chat tool with strict=true and compliant schema is forwarded with strict preserved

- **WHEN** a client sends `tools: [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": false}, "strict": true}}]`
- **THEN** the coerced Responses tool item is `{"type": "function", "name": "f", "description": null, "parameters": {...}, "strict": true}`, and the upstream request is accepted (`200`)

#### Scenario: Chat tool with strict=true and violating schema is rejected with 400

- **WHEN** a client sends a chat tool with `strict: true` but `parameters.additionalProperties` is missing or `true`
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"` and `error.param = "tools[<index>].function.parameters"`, without any upstream connection being opened

#### Scenario: Responses-shaped chat payload with flat strict tool is rejected with 400

- **WHEN** a client sends a Responses-shaped payload through `/v1/chat/completions` using `input` and a flat Responses function tool with `strict: true` but a violating `parameters` schema
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"` and `error.param = "tools[<index>].parameters"`, without any upstream connection being opened

#### Scenario: Chat tool with strict=false or omitted is forwarded with strict preserved (or absent)

- **WHEN** a client sends a chat tool without a `strict` key (or with `strict: false`)
- **THEN** the coerced Responses tool item has no `strict` key (or `strict: false`) accordingly, and no strict-mode pre-validation is run for that tool

#### Scenario: Built-in tool types are unaffected

- **WHEN** a client sends `tools: [{"type": "web_search"}]`
- **THEN** the coerced Responses tool item is unchanged (no `strict` field considered), matching pre-fix behavior

### Requirement: Chat Completions normalizes provider-specific thinking aliases

When Chat Completions clients send provider-specific reasoning controls that are commonly used by non-OpenAI SDKs, the service MUST normalize those controls into the internal Responses `reasoning` shape before forwarding upstream. The original provider-specific fields MUST NOT be forwarded upstream unchanged.

#### Scenario: Qwen-style enable_thinking is normalized

- **WHEN** a client calls `/v1/chat/completions` with `enable_thinking: true`
- **AND** no explicit `reasoning` or `reasoning_effort` override is present
- **THEN** the mapped Responses payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Anthropic-style thinking object is normalized

- **WHEN** a client calls `/v1/chat/completions` with `thinking: {"type":"enabled","budget_tokens":2048}`
- **AND** no explicit `reasoning` or `reasoning_effort` override is present
- **THEN** the mapped Responses payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `thinking`

### Requirement: Preserve tool strictness semantics in Responses-shaped chat payloads

When a Responses-shaped chat payload uses a flat Responses function tool, strict-mode validation MUST still run, and invalid strict schemas MUST be rejected before upstream is contacted. Rejection details MUST report the flat tool schema param path used by this path.

#### Scenario: Responses-shaped chat payload with flat strict tool is rejected with 400

- **WHEN** a client sends a Responses-shaped payload through `/v1/chat/completions` using `input` and a flat Responses function tool with
  `strict: true` but a violating `parameters` schema
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"` and `error.param = "tools[<index>].parameters"`
- **AND** no upstream connection is opened

### Requirement: Chat Completions reject truncated upstream Responses streams

`POST /v1/chat/completions` MUST classify upstream Responses iterator
exhaustion before a terminal `response.completed`, `response.incomplete`,
`response.failed`, or `error` event as `upstream_stream_truncated`. The error
MUST use OpenAI error type `server_error`. Partial content received before the
exhaustion MUST NOT be presented as a successfully completed non-streaming Chat
Completion.

#### Scenario: Streaming upstream EOF emits error and done

- **WHEN** a streaming Chat Completions request receives zero or more
  non-terminal upstream Responses events
- **AND** the upstream iterator reaches EOF before a terminal event
- **THEN** the proxy MUST emit an OpenAI error chunk with code
  `upstream_stream_truncated`
- **AND** the proxy MUST terminate the stream with `data: [DONE]`

#### Scenario: Collected upstream EOF returns an error envelope

- **WHEN** a non-streaming Chat Completions request receives zero or more
  non-terminal upstream Responses events
- **AND** the upstream iterator reaches EOF before a terminal event
- **THEN** the proxy MUST return HTTP 502
- **AND** the response body MUST be an OpenAI error envelope with code
  `upstream_stream_truncated` and type `server_error`
- **AND** the proxy MUST NOT return a `chat.completion` success object

#### Scenario: Explicit terminal events retain existing behavior

- **WHEN** the upstream iterator emits `response.completed`,
  `response.incomplete`, `response.failed`, or `error`
- **THEN** the proxy MUST preserve the existing Chat Completions mapping for
  that event
- **AND** the proxy MUST preserve existing usage, tool-call, and upstream
  generator cleanup behavior

### Requirement: Chat Completions passthrough fields are shape-checked, not deep-validated

The service MUST treat the `messages`, `tools` and `input` fields of `/v1/chat/completions` requests as opaque JSON: it MUST NOT re-validate or coerce their nested values against a per-field type schema. Message structure MUST be enforced by the chat mapping rules (messages are objects with a string `role` from the supported set; content, `tool_calls` and `tool_call_id` rules) and each violation MUST return a 4xx OpenAI `invalid_request_error`; because these rules run at the request level, such envelopes carry no per-item `error.param` path. Message keys the mapping does not inspect MUST NOT be rejected for their type: `refusal` of any non-string type is ignored (it contributes a refusal content part only when it is a non-empty string), `name`/`call_id`/`tool_call_id` values are type-checked only where the mapping for that role consumes them, and an assistant `tool_calls` of `null` is treated as omitted (a present, non-null `tool_calls` MUST still be an array). `tools` MUST be an array and `messages` MUST be an array when present, and neither may nest objects/arrays deeper than 200 levels; violations MUST return HTTP 400 with `error.param` naming the field. Non-finite numbers (for example `1e400`) inside these fields serialize as `null` in the mapped payload. Tool definitions MUST reach the mapped Responses tools byte-for-byte apart from the documented chat-to-Responses tool normalization.

#### Scenario: Non-array chat tools are rejected with the tools param

- **WHEN** a client sends `/v1/chat/completions` with `tools` set to `null`, a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "tools"`

#### Scenario: Non-array chat messages are rejected with the messages param

- **WHEN** a client sends `/v1/chat/completions` with `messages` set to a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "messages"`

#### Scenario: Deeply nested chat fields are rejected with the field param

- **WHEN** a client sends `/v1/chat/completions` with `messages` content or `tools[].function.parameters` nested more than 200 levels deep
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param` naming `messages` or `tools`

#### Scenario: Uninspected assistant keys are accepted regardless of type

- **WHEN** a client sends an assistant message with string `content` and `"refusal"` set to `null`, a number, a boolean, an array or an object, or with `"tool_calls": null`
- **THEN** the request is accepted
- **AND** the mapped Responses input is identical to the same message without that key

#### Scenario: Malformed message shapes are still rejected

- **WHEN** a client sends a message that is not an object, has a non-string `role`, an assistant message whose `tool_calls` is present, non-null and not an array, or a tool message whose `tool_call_id` is not a non-empty string
- **THEN** the proxy returns a 4xx OpenAI `invalid_request_error`

#### Scenario: Chat tool parameter schemas are forwarded verbatim

- **GIVEN** a chat function tool whose `function.parameters` schema contains nested objects, arrays, floats, booleans and nulls
- **WHEN** the service maps the request to Responses
- **THEN** the mapped tool's `parameters` is byte-identical to the client's JSON

### Requirement: Daybreak capability intent fails closed on Chat Completions

`POST /v1/chat/completions` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication it MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before model-source lookup, usage reservation, account selection, Responses conversion, or upstream dispatch. Headerless Chat Completions requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before chat routing

- **WHEN** a valid proxy API key sends a Chat Completions request with the Daybreak capability carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no model source, reservation, account, Responses request, or upstream attempt is selected

#### Scenario: Headerless chat behavior remains unchanged

- **WHEN** a Chat Completions request omits the required-capability carrier
- **THEN** the route retains its existing authentication, validation, routing, and response behavior

### Requirement: Chat Completions shares API-key reasoning allowlist enforcement

Before Chat Completions traffic selects a subscription account or an external
model source, the service MUST convert reasoning controls to the internal
Responses representation and apply the authenticated API key's
`allowedReasoningEfforts` policy. A rejected effort MUST produce the same
OpenAI-compatible `403` `reasoning_effort_not_allowed` result as a native
Responses request and MUST NOT call the external source.
The `thinking` string alias MUST recognize every selectable effort, including
`minimal`, before allowlist evaluation.
A snake-case `reasoning_effort` MUST still participate in authorization when a
separate `reasoning` object contains only metadata such as `summary`.
An inactive `thinking` control MUST NOT mask a separate enabled reasoning
alias during authorization.
Reasoning metadata inside `thinking` MUST be merged with enabled controls before
allowlist evaluation and MUST NOT hide their implicit `medium` effort.

After a source-routed Chat Completions request passes the policy, any accepted
`ultra` value MUST use the upstream wire value `max` regardless of whether the
client expressed it through `reasoning_effort`, `reasoningEffort`,
`reasoning.effort`, or `thinking`. If several reasoning spellings conflict,
every retained outbound spelling MUST be aligned to the authorized client-plane
effort. Other allowed client-plane efforts MUST remain unchanged for the
external source. A sole `enable_thinking: true` control authorized as `medium`
MUST remain enabled on source egress.
When source selection replaces an effort-bearing model alias with a canonical
source model slug and the client supplied no separate reasoning control, the
service MUST materialize that authorized effort as `reasoning_effort`. This
applies whether the alias came from the client model or the API key's enforced
model.

#### Scenario: Source-routed chat request is rejected before forwarding

- **GIVEN** a source-routed chat model and an API key with
  `allowedReasoningEfforts: ["low", "medium", "high"]`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "ultra"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Minimal thinking alias is evaluated before forwarding

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `thinking: "minimal"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Reasoning metadata does not mask snake-case effort

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "max"` and
  `reasoning: {"summary": "auto"}`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Disabled thinking does not mask an enabled alias

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `thinking: false` and
  `enable_thinking: true`
- **THEN** the service evaluates the enabled alias as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Thinking metadata does not mask its enabled state

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies
  `thinking: {"summary": "auto", "enabled": true}`
- **THEN** the service evaluates the enabled control as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Source-routed chat aliases use the ultra wire value

- **GIVEN** a source-routed chat model and an API key that allows `ultra`
- **WHEN** a Chat Completions client supplies `thinking: "ultra"`
- **THEN** the source receives `thinking: "max"`

#### Scenario: Source-routed chat preserves an allowed client-plane effort

- **GIVEN** a source-routed chat model and an API key that allows `minimal`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "minimal"`
- **THEN** the source receives `reasoning_effort: "minimal"`

#### Scenario: Source-routed chat preserves an authorized thinking toggle

- **GIVEN** a source-routed chat model and an API key that allows `medium`
- **WHEN** a Chat Completions client supplies only `enable_thinking: true`
- **THEN** the source receives `enable_thinking: true`

#### Scenario: Canonical source retains effort from a model alias

- **GIVEN** a source registered for `gpt-5.6-sol` and an API key that allows
  `xhigh`
- **WHEN** a Chat Completions client requests `gpt-5.6-sol-xhigh` without a
  separate reasoning control
- **THEN** the source receives model `gpt-5.6-sol`
- **AND** it receives `reasoning_effort: "xhigh"`

#### Scenario: Canonical source retains effort from an enforced model alias

- **GIVEN** a source registered for `gpt-5.6-sol` and an API key that enforces
  `gpt-5.6-sol-xhigh` and allows `xhigh`
- **WHEN** a Chat Completions client requests canonical `gpt-5.6-sol` without a
  separate reasoning control
- **THEN** the source receives model `gpt-5.6-sol`
- **AND** it receives `reasoning_effort: "xhigh"`

