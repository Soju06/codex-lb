## Why

Exact OpenAI-compatible family roots currently fall outside the fallback path
classifier. `/v1` and `/backend-api` therefore return Starlette's generic
`detail` payload while their trailing-slash and child paths return the
documented OpenAI error envelope.

## What Changes

- Classify exact `/v1` and `/backend-api` roots as members of their existing
  OpenAI-compatible path families.
- Preserve the 404 status and `not_found` envelope used by equivalent paths.
- Leave dashboard, static-asset, health, and other route families unchanged.
- Add route-level exact/slash/child regression coverage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: exact OpenAI family roots use the same local error
  envelope as equivalent family paths.

## Impact

- API surface: unmatched `/v1` and `/backend-api` requests.
- Code: centralized exception path classification.
- No route registration, redirect, dependency, database, configuration, or
  non-OpenAI behavior change.
