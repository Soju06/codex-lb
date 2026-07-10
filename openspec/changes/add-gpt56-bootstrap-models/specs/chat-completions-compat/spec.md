## MODIFIED Requirements

### Requirement: Chat Completions normalizes provider-specific thinking aliases

The service MUST normalize provider-specific Chat Completions reasoning
controls used by non-OpenAI SDKs into the internal Responses `reasoning` shape
before forwarding upstream. The original provider-specific fields MUST NOT be
forwarded upstream unchanged. String and object aliases using `max` MUST remain
`max`, while client-facing `ultra` MUST normalize to wire-level `max`.

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

#### Scenario: Chat ultra thinking alias normalizes to wire max

- **WHEN** a client calls `/v1/chat/completions` with `thinking: "ultra"`
- **AND** no explicit `reasoning` or `reasoning_effort` override is present
- **THEN** the mapped Responses payload includes `reasoning.effort: "max"`
- **AND** the forwarded upstream payload does not include `thinking`

#### Scenario: Source-routed Chat reasoning aliases send wire max

- **WHEN** an OpenAI-compatible source model supports reasoning
- **AND** a Chat Completions request includes client-facing `ultra` in top-level or nested reasoning controls
- **THEN** the source receives only canonical `max` reasoning efforts
- **AND** provider-specific `thinking` and camel-case reasoning aliases are absent
