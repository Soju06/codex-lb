## ADDED Requirements

### Requirement: Responses accept encrypted output inclusion

The service MUST accept `output.encrypted_content` in the Responses `include` array and preserve it in the forwarded payload. Existing supported selectors and rejection of unknown selectors MUST remain unchanged.

#### Scenario: Encrypted output requested

- **WHEN** a native or OpenAI-compatible Responses request includes `output.encrypted_content`, alone or alongside `reasoning.encrypted_content`
- **THEN** validation succeeds and the requested selectors are preserved

#### Scenario: Unknown selector remains invalid

- **WHEN** a request includes an unsupported selector alongside `output.encrypted_content`
- **THEN** validation rejects the request
