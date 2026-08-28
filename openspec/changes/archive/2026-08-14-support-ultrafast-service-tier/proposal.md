## Why

OpenAI introduced an access-controlled Ultrafast processing tier for `gpt-5.6-sol`. codex-lb already preserves unknown request tier strings, but its API-key policy and dashboard reject `ultrafast`, leaving the feature incomplete and untested.

## What Changes

- Accept and persist `ultrafast` as an API-key-enforced service tier.
- Expose Ultrafast in the API key create and edit controls.
- Preserve and forward the canonical `ultrafast` value through Responses-compatible routes.
- Use live upstream model-catalog entitlement data to route Ultrafast requests only to advertising accounts.
- Document the upstream availability constraint and add focused regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: allow dashboard API keys to enforce the canonical `ultrafast` tier.
- `responses-api-compat`: define pass-through behavior for explicit and enforced Ultrafast requests.
- `model-catalog-compat`: define entitlement-aware account routing for the access-controlled tier.

## Impact

The change affects API-key request validation and normalization, dashboard API-key forms and translations, Responses compatibility documentation, model-catalog routing tests, and focused backend/frontend tests. It adds no dependency, setting, database migration, or bootstrap entitlement metadata.
