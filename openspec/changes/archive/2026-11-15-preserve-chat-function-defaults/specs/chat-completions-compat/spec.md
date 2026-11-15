## MODIFIED Requirements

### Requirement: `_normalize_chat_tools` preserves the function tool strict flag

For non-empty chat `messages`, the service MUST default omitted/null function
`strict` to false while preserving non-null values, parameter schemas, and client
tool definitions. This MUST cover nested/accepted flat tools and both streaming
modes. Native Responses and Responses-shaped chat passthrough MUST retain
omission semantics. Explicit true MUST retain strict-schema checks and original
error indices. Non-function tools MUST remain unchanged.

#### Scenario: Omitted or null chat strictness remains non-strict

- **GIVEN** a nested or flat chat function with required `record_id` and optional `context`
- **WHEN** the function's `strict` is omitted or null
- **THEN** the forwarded Responses tool contains `strict: false`
- **AND** only `record_id` remains required in the unchanged parameter schema
- **AND** the original tool definition remains unchanged

#### Scenario: Explicit strictness is preserved

- **WHEN** a chat function specifies `strict: true` or `strict: false`
- **THEN** the forwarded Responses tool retains that boolean

#### Scenario: Invalid strictness is not silently defaulted

- **WHEN** a chat function supplies a non-null, non-boolean `strict` value
- **THEN** conversion preserves the value for downstream validation rather than replacing it with false

#### Scenario: Responses omission semantics are retained

- **GIVEN** a native Responses request or a Responses-shaped chat request with absent or empty `messages`
- **WHEN** a function tool omits `strict`
- **THEN** the forwarded tool continues to omit `strict`

#### Scenario: Explicit strict schemas are still validated

- **WHEN** a nested chat function sets `strict: true` with a violating parameter schema
- **THEN** the service returns HTTP 400 with `error.code = "invalid_function_parameters"`
- **AND** `error.param` identifies `tools[<original index>].function.parameters`
- **AND** no upstream connection is opened

#### Scenario: Built-in tools are unaffected

- **WHEN** a supported non-function tool is converted
- **THEN** no function strictness default is added
