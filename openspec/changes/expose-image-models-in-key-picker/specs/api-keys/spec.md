## ADDED Requirements

### Requirement: Image adapter models in dashboard selection

The dashboard model catalog SHALL include every model accepted by the Images adapter exactly once, including when the Responses registry is empty. Image adapter entries SHALL be marked `imageOnly: true`, SHALL NOT advertise reasoning efforts, and SHALL remain selectable and persistable in API-key create and edit dialogs. Automations SHALL exclude image-only models. This dashboard addition SHALL NOT inject adapter-only entries into public Responses or Codex model catalogs.

#### Scenario: Configure an image-only API key
- **WHEN** an operator searches for `gpt-image` while creating or editing an API key
- **THEN** the picker offers the supported image adapter models
- **AND** saving and reopening preserves the selected model restriction

#### Scenario: Catalog empty or containing an image slug
- **WHEN** the Responses registry is empty or already contains an image adapter slug
- **THEN** each supported image adapter model appears once with image-only metadata

#### Scenario: Automations use Responses models
- **WHEN** an operator configures an automation
- **THEN** image-only model entries are absent from its model selector

## MODIFIED Requirements

### Requirement: Public OpenAI-compatible model list filtering

OpenAI-compatible model list endpoints SHALL filter models using a single predicate that requires both conditions:
1. `model.supported_in_api` is true
2. If `allowed_models` is configured, the model is in the allowed set

The dashboard `/api/models` endpoint SHALL additionally include supported Images adapter models for API-key configuration, marked `imageOnly: true`; these entries are independent of the Responses registry and are excluded from automation selection.

For Responses registry entries, this predicate SHALL be applied consistently across `/api/models`, `/v1/models`, and the OpenAI-style `data` alias in `/backend-api/codex/models`. The Codex-native `models` catalog in `/backend-api/codex/models` SHALL also expose unsupported upstream models only when the model is a Codex shell-command model (`shell_type="shell_command"`); unsupported non-shell models SHALL remain hidden.

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
- **THEN** that model is included in `/backend-api/codex/models.models`
- **AND** that model is not included in `/backend-api/codex/models.data`
- **AND** that model is not included in `/api/models` or `/v1/models`

#### Scenario: Allowed but unsupported model excluded

- **WHEN** a Responses registry model that is not an Images adapter model is in the `allowed_models` set but has `supported_in_api=false`
- **AND** the model is not a Codex shell-command model
- **THEN** that model is not exposed in any model list endpoint

#### Scenario: gpt-5.3-codex aliases share availability gate consistently

- **WHEN** `gpt-5.3-codex` has `supported_in_api=false`
- **AND** `gpt-5.3-codex-spark` has `supported_in_api=true`
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data`
      expose `gpt-5.3-codex-spark` but do not expose `gpt-5.3-codex`

#### Scenario: Consistent model set across endpoints

- **GIVEN** any model registry state
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data` expose the same OpenAI-compatible set of Responses registry models
- **AND** `/api/models` additionally exposes supported Images adapter models for key configuration
