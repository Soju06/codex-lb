## ADDED Requirements

### Requirement: Preserve Chat Completions function strictness defaults

When converting a request with non-empty chat `messages` to Responses, the service
MUST emit `strict: false` for function tools whose strictness is omitted or null.
It MUST preserve explicit boolean strictness and MUST NOT change the tool's
parameter schema or mutate the client's tool definition. This applies to nested
Chat Completions function definitions and accepted flat function definitions.
The rule MUST apply to streaming and non-streaming Chat Completions requests.
Native Responses requests and Responses-shaped chat requests with absent or empty
`messages` MUST retain their original strictness and omission semantics.

#### Scenario: Omitted chat strictness stays non-strict upstream

- **GIVEN** a chat function tool with required `record_id` and optional `grep` and `context`
- **WHEN** the client omits `strict` or sets it to null
- **THEN** the forwarded Responses function tool contains `strict: false`
- **AND** only `record_id` remains required in the unchanged parameter schema

#### Scenario: Explicit chat strictness is preserved

- **WHEN** a chat function tool explicitly sets `strict` to true or false
- **THEN** the forwarded Responses function tool retains that boolean value

#### Scenario: Responses omission is preserved

- **GIVEN** a native Responses request or a Responses-shaped chat passthrough request
- **WHEN** the function tool omits `strict`
- **THEN** the forwarded tool continues to omit `strict`

## MODIFIED Requirements

### Requirement: `_normalize_chat_tools` preserves the function tool strict flag

The chat → responses coercion pipeline MUST preserve the `strict` field on `function` tools when normalizing the chat-completions `tools[]` array into the Responses-API tool item shape. Specifically, when a chat tool of shape `{ "type": "function", "function": { "name": "...", "parameters": {...}, "strict": <bool> } }` is normalized, the emitted Responses tool item MUST set `"strict": <bool>` (mirroring the inbound value) and not silently drop it.

Function tools with explicit `strict: true` MUST retain strict-mode schema validation. Validation errors MUST identify the tool index in the original client payload.

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

#### Scenario: Chat tool with strict=false, null, or omitted is forwarded as non-strict

- **WHEN** a request with non-empty chat `messages` contains a function tool whose `strict` flag is false, null, or omitted
- **THEN** the coerced Responses tool item contains `strict: false`
- **AND** no strict-mode pre-validation is run for that tool

#### Scenario: Built-in tool types are unaffected

- **WHEN** a client sends `tools: [{"type": "web_search"}]`
- **THEN** the coerced Responses tool item is unchanged (no `strict` field considered), matching pre-fix behavior
