## ADDED Requirements

### Requirement: Chat Completions passthrough fields are shape-checked, not deep-validated

The service MUST treat the `messages`, `tools` and `input` fields of `/v1/chat/completions` requests as opaque JSON: it MUST NOT re-validate or coerce their nested values against a per-field type schema. Message structure MUST be enforced by the chat mapping rules (messages are objects with a string `role` from the supported set; content, `tool_calls` and `tool_call_id` rules) and each violation MUST return a 4xx OpenAI `invalid_request_error`. Message keys the mapping does not inspect (for example `refusal`, `name`) MUST NOT be rejected for their type; `refusal` contributes a refusal content part only when it is a non-empty string. `tools` MUST be an array and `messages` MUST be an array when present; violations MUST return HTTP 400 with `error.param` naming the field. Tool definitions MUST reach the mapped Responses tools byte-for-byte apart from the documented chat-to-Responses tool normalization.

#### Scenario: Non-array chat tools are rejected with the tools param

- **WHEN** a client sends `/v1/chat/completions` with `tools` set to `null`, a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "tools"`

#### Scenario: Non-array chat messages are rejected with the messages param

- **WHEN** a client sends `/v1/chat/completions` with `messages` set to a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "messages"`

#### Scenario: Assistant refusal null is accepted

- **WHEN** a client sends an assistant message with `"refusal": null` alongside string `content`
- **THEN** the request is accepted
- **AND** the mapped Responses input is identical to the same message without the `refusal` key

#### Scenario: Malformed message shapes are still rejected

- **WHEN** a client sends a message that is not an object, has a non-string `role`, an assistant message whose `tool_calls` is not an array, or a tool message whose `tool_call_id` is not a non-empty string
- **THEN** the proxy returns a 4xx OpenAI `invalid_request_error`

#### Scenario: Chat tool parameter schemas are forwarded verbatim

- **GIVEN** a chat function tool whose `function.parameters` schema contains nested objects, arrays, floats, booleans and nulls
- **WHEN** the service maps the request to Responses
- **THEN** the mapped tool's `parameters` is byte-identical to the client's JSON
