# Preserve function tool strict flag in chat → responses coercion

## ADDED Requirements

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

#### Scenario: Chat tool with strict=false or omitted is forwarded with strict preserved (or absent)

- **WHEN** a client sends a chat tool without a `strict` key (or with `strict: false`)
- **THEN** the coerced Responses tool item has no `strict` key (or `strict: false`) accordingly, and no strict-mode pre-validation is run for that tool

#### Scenario: Built-in tool types are unaffected

- **WHEN** a client sends `tools: [{"type": "web_search"}]`
- **THEN** the coerced Responses tool item is unchanged (no `strict` field considered), matching pre-fix behavior
