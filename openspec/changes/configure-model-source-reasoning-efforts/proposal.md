## Why

OpenAI-compatible Model Sources can currently opt a model into reasoning, but
they cannot declare which reasoning efforts the model accepts or which effort
should be used by default. Clients may therefore expose an incorrect selector
or send the unsupported `none` effort to providers such as Muse.

## What Changes

- Add operator controls to configure supported reasoning efforts and a default
  effort for each Model Source model.
- Persist the configuration in the existing model raw metadata field without
  introducing new credentials, settings, or migrations.
- Publish the configured levels and default through the existing OpenAI model
  catalog representation used by `/v1/models`.
- Accept both compact string levels and descriptive `{effort, description}`
  metadata when reading existing configurations.
- Preserve existing Model Source behavior when reasoning is disabled or no
  effort metadata is configured.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-catalog-compat`: Define the reasoning-level metadata exposed for
  OpenAI-compatible Model Source models.
- `frontend-architecture`: Define the Model Source reasoning configuration
  controls and persistence behavior.

## Impact

- Model Source catalog conversion and dashboard create/edit forms.
- English, Korean, and Simplified Chinese Model Source copy.
- Focused backend catalog and frontend form regression coverage.
- No fallback routing, subscription selection, database migration, or API-key
  accounting behavior changes.
