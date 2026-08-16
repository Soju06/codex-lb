# Reject Duplicate API-key Limit Rules

## Why

`POST /api/api-keys` accepts multiple limit rules with the same
`(limit_type, limit_window, model_filter)` identity, even though the edit path
rejects them. The duplicate rows are enforced independently, so an accidental
duplicate can silently make a newly created key stricter than configured.

## What Changes

- Validate limit-rule identity uniqueness before creating an API key.
- Reuse the same validation for create and update paths.
- Return the existing typed API-key validation error, identify the duplicated
  rule, and persist no key when a create payload contains duplicate rules.

## Impact

- Affected spec: `api-keys`
- Affected code: `app/modules/api_keys/service.py`
- Affected route: `POST /api/api-keys`
