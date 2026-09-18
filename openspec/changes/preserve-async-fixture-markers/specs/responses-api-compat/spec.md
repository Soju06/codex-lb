## ADDED Requirements

### Requirement: Fixture sanitization preserves asynchronous tool-call markers

The request-fixture sanitizer MUST preserve an optional boolean `async` field on `function_call` and `custom_tool_call` items. It MUST keep an absent marker absent, retain call/output pairing through identifier remapping, replace captured free text, and remain idempotent. The marker MUST NOT become an allowed field on output items or unrelated input item types.

#### Scenario: A captured asynchronous call is rebuilt

- **GIVEN** a function or custom tool call with a boolean async marker and its matching output
- **WHEN** the request fixture is sanitized twice
- **THEN** both rebuilt requests preserve the marker value and matching call IDs
- **AND** captured input and output text are replaced on the first pass
- **AND** the second pass does not change the rebuilt request

#### Scenario: A synchronous capture has no marker

- **GIVEN** a function or custom tool call with no async field
- **WHEN** its request fixture is sanitized
- **THEN** the sanitizer does not introduce an async field
