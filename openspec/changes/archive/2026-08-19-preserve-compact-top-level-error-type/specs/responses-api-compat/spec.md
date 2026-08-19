## ADDED Requirements

### Requirement: Compact terminal SSE errors preserve top-level error type

When the compact Responses upstream terminates with a top-level SSE `type=error` frame, the proxy MUST preserve a supplied non-blank `error_type` in the emitted OpenAI error envelope. If `error_type` is absent, non-string, or blank, the proxy MUST use `server_error`. The proxy MUST preserve existing status, code, message, and parameter mapping, and MUST NOT alter nested OpenAI-style error-envelope behavior.

#### Scenario: Top-level invalid request type is preserved

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is `invalid_request_error`
- **THEN** the proxy returns HTTP 400 with `error.type=invalid_request_error`
- **AND** preserves the frame's code, message, and parameter

#### Scenario: Missing or blank top-level type uses compatibility fallback

- **WHEN** compact upstream terminates with a top-level `type=error` frame whose `error_type` is absent or blank
- **THEN** the emitted OpenAI error envelope uses `error.type=server_error`
- **AND** existing status, code, message, and parameter mapping remains unchanged

#### Scenario: Nested compact error envelope remains unchanged

- **WHEN** compact upstream terminates with a nested OpenAI-style error envelope
- **THEN** the proxy preserves the nested type and all other mapped fields using the existing parser
