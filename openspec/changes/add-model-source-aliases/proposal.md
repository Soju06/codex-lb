## Why

Custom endpoints often expose opaque model IDs. Operators need stable, readable client model aliases while retaining the exact upstream ID and existing model capabilities.

## What Changes

- Add an optional per-model upstream ID in source metadata, with identity mapping by default.
- Allow the dashboard Models field to express `client-alias=upstream-id` alongside ordinary model IDs.
- Map only the outbound model field after source selection; retain the alias in catalogs, response model fields, authorization, continuity and accounting.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: validated aliases and forwarding across supported source HTTP routes.
- `model-catalog-compat`: advertise client aliases while keeping upstream routing configuration private.

## Impact

Model-source metadata validation, dashboard create/edit forms, HTTP source forwarding and route regression tests. No database migration, new environment setting, upstream transport, or token-pooling behavior.
