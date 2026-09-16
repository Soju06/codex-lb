## Why

The API-key allowed-models picker only receives subscription catalog and custom-source models. The Images API accepts four image models that upstream Codex catalogs normally omit, preventing operators from selecting them when restricting a key.

## What Changes

- Supplement the dashboard model catalog with the exact image model IDs accepted by the Images adapter.
- Deduplicate image entries against subscription and source models and advertise no reasoning efforts for adapter-provided entries.
- Verify image model selection and persistence through the API-key create/edit flows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Clarify dashboard-only adapter model supplementation and require supported image models in the API-key picker.

## Impact

Changes affect the Images model allowlist, dashboard `GET /api/models`, and regression tests. No settings, database migrations, dependencies, public proxy catalog changes, or image-generation requests are required.
