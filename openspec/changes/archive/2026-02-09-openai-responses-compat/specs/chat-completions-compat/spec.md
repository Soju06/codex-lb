## ADDED Requirements

### Requirement: Validate Chat Completions requests
The service MUST accept POST requests to `/v1/chat/completions` with a JSON body and MUST validate required fields according to OpenAI Chat Completions expectations. The request MUST include `model` and a non-empty `messages` array of objects. Invalid payloads MUST return a 4xx response with an OpenAI error envelope.

#### Scenario: Minimal valid chat request
- **WHEN** the client sends `{ "model": "gpt-4.1", "messages": [{"role":"user","content":"hi"}] }`
- **THEN** the service accepts the request and begins a response (streaming or non-streaming based on `stream`)

#### Scenario: Invalid messages payload
- **WHEN** the client sends an empty `messages` array or non-object items
- **THEN** the service returns a 4xx response with an OpenAI error envelope describing the invalid parameter

### Requirement: Enforce message content type rules
The service MUST enforce role-specific message content rules: `system` and `developer` messages MUST contain text-only content, while `user` messages MAY contain text, image, audio, or file content parts per OpenAI chat spec. Unsupported content types MUST return an OpenAI error envelope.

#### Scenario: Non-text system message
- **WHEN** a `system` or `developer` message includes a non-text content part
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating an invalid message content type

#### Scenario: User message with mixed content
- **WHEN** a `user` message includes a mix of text and image parts
- **THEN** the service accepts the request and forwards the content parts in order

### Requirement: Map chat requests to Responses wire format
The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, and `response_format` MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: Tool choice values
- **WHEN** the client sets `tool_choice` to `none`, `auto`, or `required`
- **THEN** the service forwards the value consistently in the mapped Responses request

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

### Requirement: Audio input formats are validated
When a `user` message includes audio input, the service MUST accept only supported formats (e.g., `wav`, `mp3`) and MUST reject unsupported formats with an OpenAI error envelope.

#### Scenario: Unsupported audio format
- **WHEN** a `user` message includes audio input in an unsupported format
- **THEN** the service returns a 4xx response with an OpenAI error envelope

### Requirement: Error mapping for chat requests
For upstream failures or invalid requests, the service MUST return an OpenAI error envelope for non-streaming responses and MUST emit an error chunk followed by `data: [DONE]` for streaming responses. Error `code`, `type`, and `message` MUST be preserved or normalized into stable values.

#### Scenario: Streaming error
- **WHEN** the upstream returns a failure during streaming
- **THEN** the service emits an error chunk and terminates the stream with `data: [DONE]`
