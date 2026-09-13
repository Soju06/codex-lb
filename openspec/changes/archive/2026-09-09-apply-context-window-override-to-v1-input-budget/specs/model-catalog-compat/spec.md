## MODIFIED Requirements

### Requirement: OpenAI-compatible model metadata uses backend context windows

When serving `GET /v1/models`, the system SHALL expose `metadata.context_window` as the upstream backend `context_window` budget by default. The system MUST NOT promote raw `max_context_window` values or hard-coded full-context guesses into `metadata.context_window`. Explicit operator context-window overrides remain the highest-priority reported-context value, clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`.

#### Scenario: GPT-5 Codex models are reported with the backend context window on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.3-codex`, or `gpt-5.4` with `context_window=272000`
- **THEN** `GET /v1/models` returns each entry with `metadata.context_window=272000`

#### Scenario: raw max_context_window does not inflate /v1/models context_window

- **WHEN** the upstream model catalog contains a model with `context_window=272000` and `max_context_window=900000`
- **THEN** `GET /v1/models` returns that entry with `metadata.context_window=272000`

### Requirement: OpenAI-compatible model metadata preserves the backend input budget explicitly

When serving `GET /v1/models`, the system SHALL expose the upstream backend input/context budget in `metadata.input_context_window`. When an explicit operator context-window override applies to a model, that override SHALL be the reported input budget as well, clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`, so `metadata.input_context_window` and the OpenAI-compatible `context_length`, `contextLength`, and `capabilities.context_length` fields never contradict `metadata.context_window` and never advertise more input than the backend sanctions. A `max_context_window` equal to the backend `context_window` — the parseability default synthesized for bootstrap and source-catalog models — MUST NOT clamp an override, so raise overrides for those models keep working. For models whose reported `metadata.context_window` is not operator-overridden, `metadata.context_window` and `metadata.input_context_window` SHOULD be equal. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models when that output-budget value is known; that value MUST NOT be used to inflate `metadata.context_window`.

#### Scenario: /v1/models exposes the 272k backend input budget explicitly

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window=272000`

#### Scenario: Explicit reported-context overrides do not hide the backend input budget

- **WHEN** an operator override sets a model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and no `max_context_window`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=515000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `515000`

#### Scenario: An override never advertises more input than the backend ceiling

- **WHEN** an operator override sets a model's reported context window to `1000000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and `max_context_window=872000`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=872000`
- **AND** `metadata.input_context_window=872000`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `872000`

#### Scenario: A synthesized ceiling equal to the backend budget does not clamp an override

- **WHEN** an operator override sets a source-catalog model's reported context window to `32768`
- **AND** that model declares `context_window=8192` and no explicit `max_context_window`, so the catalog synthesizes `max_context_window=8192`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=32768`
- **AND** `metadata.input_context_window=32768`
- **AND** `context_length`, `contextLength`, and `capabilities.context_length` of `32768`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`

### Requirement: Codex-native model catalog keeps backend catalog fields

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the backend compact/input budget unless an explicit operator override applies, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` compatibility metadata MUST NOT mutate the native Codex endpoint.

When an explicit operator context-window override applies to a model, the native entry SHALL report the single resolved value — the override clamped to the upstream-declared `max_context_window` when upstream declares one above the backend `context_window`; a `max_context_window` equal to the backend `context_window` (the synthesized parseability default) MUST NOT clamp — on `context_window`, and SHALL rewrite `max_context_window` to that same resolved value when upstream provides the field. The endpoint's OpenAI-compatible `data` alias SHALL report the same resolved value on its `context_length`, `contextLength`, `capabilities.context_length`, `metadata.context_window`, and `metadata.input_context_window` fields, so the native and alias views of one model never advertise different budgets.

#### Scenario: Native Codex route preserves compact budget

- **WHEN** the upstream model catalog contains `gpt-5.5` with `context_window=272000`
- **THEN** `GET /backend-api/codex/models` returns `gpt-5.5.context_window=272000`
- **AND** it does not replace that field with `400000`

#### Scenario: Codex model catalog also exposes OpenAI data alias

- **WHEN** a client requests `GET /backend-api/codex/models`
- **THEN** the response keeps the Codex-native `models` list
- **AND** the response includes `object: "list"` and an OpenAI-compatible `data` list
- **AND** `data` contains model entries whose Codex visibility is `list`
- **AND** `data` excludes entries whose Codex visibility is `hide`

#### Scenario: Native Codex catalog reports one resolved budget for a clamped override

- **WHEN** an operator override sets a model's reported context window to `1000000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and `max_context_window=872000`
- **THEN** `GET /backend-api/codex/models` returns that model with `context_window=872000`
- **AND** `max_context_window=872000`

#### Scenario: Codex data alias reports the resolved input budget for an override

- **WHEN** an operator override sets a model's reported context window to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000` and no explicit `max_context_window`
- **THEN** the `GET /backend-api/codex/models` `data` alias entry for that model reports `context_length`, `contextLength`, and `capabilities.context_length` of `515000`
- **AND** `metadata.context_window=515000` and `metadata.input_context_window=515000`
- **AND** the native `models` entry reports `context_window=515000`
