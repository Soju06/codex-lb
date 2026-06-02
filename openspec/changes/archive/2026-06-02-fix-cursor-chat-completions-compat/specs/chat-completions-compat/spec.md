## MODIFIED Requirements

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

### Requirement: Preserve tool strictness semantics in Responses-shaped chat payloads

When a Responses-shaped chat payload uses a flat Responses function tool, strict-mode validation MUST still run, and invalid strict schemas MUST be rejected before upstream is contacted. Rejection details MUST report the flat tool schema param path used by this path.

#### Scenario: Responses-shaped chat payload with flat strict tool is rejected with 400

- **WHEN** a client sends a Responses-shaped payload through `/v1/chat/completions` using `input` and a flat Responses function tool with
  `strict: true` but a violating `parameters` schema
- **THEN** the proxy returns `HTTP 400` with `error.code = "invalid_function_parameters"` and `error.param = "tools[<index>].parameters"`
- **AND** no upstream connection is opened
