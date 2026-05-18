## ADDED Requirements

### Requirement: OpenAI-compatible model metadata uses full context windows

When serving `GET /v1/models`, the system SHALL expose `metadata.context_window` as the model's full context window for generic OpenAI-compatible clients, not the Codex-native compact-budget window. The system MUST report these full-context values for known GPT-5 Codex models:

| model | `metadata.context_window` |
| --- | ---: |
| `gpt-5.4` | `1000000` |
| `gpt-5.5` | `400000` |
| `gpt-5.4-mini` | `400000` |
| `gpt-5.3-codex` | `400000` |

For models without a known full-context override, the system SHOULD use an upstream `max_context_window` value when it is a positive integer larger than the Codex-native `context_window`; otherwise it MAY fall back to the Codex-native `context_window`. Explicit operator context-window overrides remain the highest-priority reported-context value.

#### Scenario: GPT-5.4 is reported as a 1M context model on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.4` with `context_window=272000` and `max_context_window=1000000`
- **THEN** `GET /v1/models` returns the `gpt-5.4` entry with `metadata.context_window=1000000`

#### Scenario: 400k GPT-5 Codex models are reported with full context on /v1/models

- **WHEN** the upstream model catalog contains `gpt-5.5`, `gpt-5.4-mini`, or `gpt-5.3-codex` with `context_window=272000`
- **THEN** `GET /v1/models` returns each entry with `metadata.context_window=400000`

### Requirement: OpenAI-compatible model metadata preserves Codex input-budget semantics separately

When serving `GET /v1/models`, the system SHALL preserve the Codex-native compact/input budget in `metadata.input_context_window` when that budget differs from `metadata.context_window`. The system SHOULD expose `metadata.max_output_tokens` for known GPT-5 Codex models whose full-context window is derived by reserving output/reasoning budget from the full context; for `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.3-codex`, that value is `128000`.

#### Scenario: /v1/models exposes the 272k Codex input budget separately

- **WHEN** the upstream model catalog contains a known GPT-5 Codex model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.input_context_window=272000`
- **AND** `metadata.context_window` remains the model's full context window

#### Scenario: Explicit reported-context overrides do not hide the Codex input budget

- **WHEN** an operator override sets a known GPT-5 Codex model's reported `metadata.context_window` to `515000`
- **AND** the upstream model catalog contains that model with `context_window=272000`
- **THEN** `GET /v1/models` returns that model with `metadata.context_window=515000`
- **AND** `metadata.input_context_window=272000`

#### Scenario: /v1/models exposes max output budget for known GPT-5 Codex models

- **WHEN** `GET /v1/models` returns `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, or `gpt-5.3-codex`
- **THEN** the entry's metadata includes `max_output_tokens=128000`

### Requirement: Codex-native model catalog keeps upstream compact-budget fields

When serving `GET /backend-api/codex/models`, the system MUST keep Codex-native model catalog semantics unchanged: the top-level `context_window` field remains the upstream Codex compact/input budget, and upstream raw fields such as `max_context_window` remain available when upstream provides them. The `/v1/models` full-context normalization MUST NOT change this native Codex endpoint.

#### Scenario: Native Codex route preserves compact budget

- **WHEN** the upstream model catalog contains `gpt-5.5` with `context_window=272000`
- **THEN** `GET /backend-api/codex/models` returns `gpt-5.5.context_window=272000`
- **AND** it does not replace that field with `400000`
