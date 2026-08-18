## MODIFIED Requirements

### Requirement: OpenAI-compatible model metadata preserves the backend input budget explicitly

When serving `GET /v1/models`, the system SHALL expose the upstream backend input/context budget in `metadata.input_context_window`. When an explicit operator context-window override applies to a model, that override SHALL be the reported input budget as well, clamped to the upstream-declared `max_context_window` when upstream provides one, so `metadata.input_context_window` and the OpenAI-compatible `context_length`, `contextLength`, and `capabilities.context_length` fields never contradict `metadata.context_window` and never advertise more input than the backend sanctions. For models whose reported `metadata.context_window` is not operator-overridden, `metadata.context_window` and `metadata.input_context_window` SHOULD be equal. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models when that output-budget value is known; that value MUST NOT be used to inflate `metadata.context_window`.

#### Scenario: /v1/models exposes the 272k backend input budget explicitly

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window=272000`

#### Scenario: Explicit reported-context overrides apply to the input budget

- **WHEN** an operator override sets a model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and no `max_context_window`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=515000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `515000`

#### Scenario: An override never advertises more input than the backend ceiling

- **WHEN** an operator override sets a model's reported context window to `1000000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and `max_context_window=872000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=872000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `872000`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`
