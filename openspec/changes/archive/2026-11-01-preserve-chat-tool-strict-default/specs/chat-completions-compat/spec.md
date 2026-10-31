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
