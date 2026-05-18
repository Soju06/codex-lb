## ADDED Requirements

### Requirement: OpenAI-compatible Responses payload sanitation removes provider-specific thinking aliases

The shared OpenAI-compatible Responses sanitation path MUST normalize third-party thinking aliases into the canonical `reasoning` object before upstream forwarding. Unknown provider-specific thinking controls MUST NOT be passed through unchanged to the upstream ChatGPT backend.

#### Scenario: Shared payload sanitation maps enable_thinking

- **WHEN** an internal Responses payload contains `enable_thinking: true`
- **AND** no explicit `reasoning.effort` is already present
- **THEN** the forwarded upstream payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Explicit reasoning wins over provider aliases

- **WHEN** an internal Responses payload contains both `reasoning: {"effort":"high"}` and `thinking: {"type":"enabled"}`
- **THEN** the forwarded upstream payload keeps `reasoning.effort: "high"`
- **AND** the forwarded upstream payload does not include `thinking`
