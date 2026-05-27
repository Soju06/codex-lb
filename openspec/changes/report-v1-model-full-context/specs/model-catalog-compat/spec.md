## ADDED Requirements

### Requirement: OpenAI-compatible model metadata uses backend context windows

When serving `GET /v1/models`, the system SHALL expose `metadata.context_window` as the upstream backend `context_window` budget by default. The system MUST NOT promote raw `max_context_window` values or hard-coded full-context guesses into `metadata.context_window`. Explicit operator context-window overrides remain the highest-priority reported-context value.

#### Scenario: GPT-5 Codex models are reported with the backend context window on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex`, or `gpt-5.4` with `context_window=272000`
- **THEN** `GET /v1/models` returns each entry with `metadata.context_window=272000`

#### Scenario: raw max_context_window does not inflate /v1/models context_window

- **WHEN** the upstream model catalog contains a model with `context_window=272000` and `max_context_window=900000`
- **THEN** `GET /v1/models` returns that entry with `metadata.context_window=272000`

### Requirement: OpenAI-compatible model metadata preserves the backend input budget explicitly

When serving `GET /v1/models`, the system SHALL expose the upstream backend input/context budget in `metadata.input_context_window`. For models whose reported `metadata.context_window` is not operator-overridden, `metadata.context_window` and `metadata.input_context_window` SHOULD be equal. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models when that output-budget value is known; that value MUST NOT be used to inflate `metadata.context_window`.

#### Scenario: /v1/models exposes the 272k backend input budget explicitly

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window=272000`

#### Scenario: Explicit reported-context overrides do not hide the backend input budget

- **WHEN** an operator override sets a model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=272000`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`

### Requirement: Codex-native model catalog keeps backend catalog fields

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the backend compact/input budget unless an explicit operator override applies, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` compatibility metadata MUST NOT mutate the native Codex endpoint.

#### Scenario: Native Codex route preserves compact budget

- **WHEN** the upstream model catalog contains `gpt-5.5` with `context_window=272000`
- **THEN** `GET /backend-api/codex/models` returns `gpt-5.5.context_window=272000`
- **AND** it does not replace that field with `400000`
