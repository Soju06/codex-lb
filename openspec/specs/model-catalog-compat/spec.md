# model-catalog-compat Specification

## Purpose
TBD - created by archiving change populate-bootstrap-model-metadata. Update Purpose after archive.
## Requirements
### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. This static catalog is a
bundled fallback for startup/offline paths; refreshed upstream model-registry
data remains the authoritative source once available. The bootstrap catalog MUST
include `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`,
`gpt-5.3-codex-spark`, `gpt-5.2`, and `codex-auto-review`, and MUST NOT invent
unverified variant slugs such as `gpt-5.5-pro`.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response does not include `gpt-5.5-pro`

#### Scenario: Codex-native models endpoint serves bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** entries such as `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`, and `codex-auto-review` include representative upstream metadata including client version, context-window, visibility, modality, plan-availability, and reasoning/verbosity fields where known

### Requirement: Refreshed upstream model data remains authoritative

The system MUST treat a refreshed upstream model-registry snapshot as
authoritative over the static bootstrap catalog. Once that snapshot exists,
model catalog endpoints and model-behavior lookups MUST use the refreshed
snapshot instead of the static bootstrap catalog. Before refresh, websocket
preference lookup and account plan filtering MUST use bootstrap model metadata
when the requested slug matches a bootstrap entry.

#### Scenario: Refreshed snapshot replaces bootstrap catalog

- **GIVEN** the model registry has a refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models` or `GET /backend-api/codex/models`
- **THEN** the response is built from the refreshed snapshot
- **AND** bootstrap-only entries are not added to the response

#### Scenario: Bootstrap websocket preference is honored before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** websocket preference is checked for a bootstrap model marked as websocket-preferred
- **THEN** the lookup returns that bootstrap preference

#### Scenario: Bootstrap plan metadata filters accounts before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **AND** a bootstrap model excludes a plan from its plan-availability metadata
- **WHEN** account selection is requested for that bootstrap model
- **THEN** accounts on excluded plans are not selected for that model

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

