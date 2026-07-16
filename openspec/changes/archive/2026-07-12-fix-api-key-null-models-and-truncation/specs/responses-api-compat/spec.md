## ADDED Requirements

### Requirement: Responses request compatibility controls

The system SHALL accept OpenAI-compatible Responses request controls that clients may send for `/v1/responses` and `/backend-api/codex/responses` when those controls can be safely normalized before the ChatGPT-backed upstream request. Specifically, `truncation` values `"auto"` and `"disabled"` MUST pass request validation and MUST be omitted from the upstream payload because the current ChatGPT-backed path does not consume the field. Unsupported `truncation` values MUST still be rejected with HTTP 400.

#### Scenario: Truncation auto is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "auto"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`

#### Scenario: Truncation disabled is accepted and stripped

- **WHEN** a client sends a Responses request with `truncation: "disabled"`
- **THEN** codex-lb accepts the request
- **AND** the upstream payload does not include `truncation`
