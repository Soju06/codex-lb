## Why

The Images adapter already accepts models which may be absent from the Responses registry. Reuse its allowlist; preserve dashboard RBAC and source catalog behavior. No new model support or provider pricing is introduced. A key restricted to gpt-image-2 is a concrete example. Empty registries and slug collisions must not hide or duplicate these entries.

## What Changes

- Image adapter models in dashboard selection.
- Include regression coverage and before/after dashboard evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: image adapter models in dashboard selection.

## Impact

Focused dashboard changes; the model-picker change also extends dashboard model metadata. No migrations, new settings, dependencies, navigation items, or setup steps.
