## ADDED Requirements

### Requirement: Chat Completions normalizes provider-specific thinking aliases

When Chat Completions clients send provider-specific reasoning controls that are commonly used by non-OpenAI SDKs, the service MUST normalize those controls into the internal Responses `reasoning` shape before forwarding upstream. The original provider-specific fields MUST NOT be forwarded upstream unchanged.

#### Scenario: Qwen-style enable_thinking is normalized

- **WHEN** a client calls `/v1/chat/completions` with `enable_thinking: true`
- **AND** no explicit `reasoning` or `reasoning_effort` override is present
- **THEN** the mapped Responses payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `enable_thinking`

#### Scenario: Anthropic-style thinking object is normalized

- **WHEN** a client calls `/v1/chat/completions` with `thinking: {"type":"enabled","budget_tokens":2048}`
- **AND** no explicit `reasoning` or `reasoning_effort` override is present
- **THEN** the mapped Responses payload includes `reasoning.effort: "medium"`
- **AND** the forwarded upstream payload does not include `thinking`
