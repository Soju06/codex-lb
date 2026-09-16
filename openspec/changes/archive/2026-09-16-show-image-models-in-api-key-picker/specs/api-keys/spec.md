## MODIFIED Requirements

### Requirement: Public OpenAI-compatible model list filtering

OpenAI-compatible model list endpoints SHALL filter models using a single predicate that requires both conditions:
1. `model.supported_in_api` is true
2. If `allowed_models` is configured, the model is in the allowed set

For subscription registry entries, this predicate SHALL be applied consistently across `/api/models`, `/v1/models`, and the OpenAI-style `data` alias in `/backend-api/codex/models`. The Codex-native `models` catalog in `/backend-api/codex/models` SHALL also expose unsupported upstream models only when the model is a Codex shell-command model (`shell_type="shell_command"`); unsupported non-shell models SHALL remain hidden.

#### Scenario: Unsupported model excluded from /v1/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **THEN** that model is not included in the `/v1/models` response

#### Scenario: Unsupported non-shell model excluded from /backend-api/codex/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **AND** the model is not a Codex shell-command model
- **THEN** that model is not included in the `/backend-api/codex/models` response

#### Scenario: Unsupported Codex shell model included only in Codex-native catalog

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **AND** the model has `shell_type="shell_command"`
- **AND** the model is not independently supported by the Images adapter
- **THEN** that model is included in `/backend-api/codex/models.models`
- **AND** that model is not included in `/backend-api/codex/models.data`
- **AND** that model is not included in `/api/models` or `/v1/models`

#### Scenario: Allowed but unsupported model excluded

- **WHEN** a model is in the `allowed_models` set but has `supported_in_api=false`
- **AND** the model is not a Codex shell-command model
- **AND** the model is not independently supported by the Images adapter
- **THEN** that model is not exposed in any model list endpoint

#### Scenario: gpt-5.3-codex aliases share availability gate consistently

- **WHEN** `gpt-5.3-codex` has `supported_in_api=false`
- **AND** `gpt-5.3-codex-spark` has `supported_in_api=true`
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data`
      expose `gpt-5.3-codex-spark` but do not expose `gpt-5.3-codex`

#### Scenario: Consistent model set across endpoints

- **GIVEN** any model registry state
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data` expose the same OpenAI-compatible set of subscription models
- **AND** `/api/models` additionally exposes supported Images adapter models for API-key policy selection without adding those adapter entries to public proxy catalogs


## ADDED Requirements

### Requirement: API-key model picker includes supported image models

The dashboard `GET /api/models` endpoint MUST include `gpt-image-2`, `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini`, as defined by the Images adapter's supported-model allowlist, even when the subscription catalog omits them or is empty. Each ID MUST occur once across subscription, adapter, and enabled source entries. Public subscription entries MUST retain their metadata on collisions; adapter-provided image entries MUST have `imageOnly=true`, `sourceOnly=false`, `supportedReasoningEfforts=[]`, and `defaultReasoningEffort=null`. The API-key create and edit dialogs MUST allow selecting these IDs, saving them in `allowedModels`, and restoring the saved selection when reopened. The Automations model picker MUST exclude image-only adapter entries while continuing to offer eligible subscription models.

#### Scenario: Images remain selectable across registry states

- **GIVEN** a bootstrap, refreshed, or empty subscription catalog that omits image models
- **WHEN** the dashboard requests `GET /api/models`
- **THEN** all four supported Images model IDs appear alongside eligible subscription and source entries
- **AND** adapter-provided image entries advertise no reasoning efforts

#### Scenario: Duplicate image IDs appear once

- **GIVEN** a supported image ID also occurs in the subscription catalog or an enabled model source
- **WHEN** the dashboard requests `GET /api/models`
- **THEN** the ID appears exactly once
- **AND** an existing public subscription entry retains its metadata

#### Scenario: Create and update an image-restricted key

- **WHEN** an operator creates an API key selecting `gpt-image-2` in the allowed-models picker
- **THEN** the saved key has `allowedModels: ["gpt-image-2"]`
- **AND** reopening the edit dialog restores that selection
- **WHEN** the operator replaces the selection with `gpt-image-1-mini` and saves
- **THEN** reopening the edit dialog shows the updated selection

#### Scenario: Automation picker excludes image-only models

- **GIVEN** the dashboard model response includes subscription, source-only and image-only entries
- **WHEN** an operator opens the Automations model picker
- **THEN** eligible subscription models are selectable
- **AND** image-only and source-only entries are not offered
