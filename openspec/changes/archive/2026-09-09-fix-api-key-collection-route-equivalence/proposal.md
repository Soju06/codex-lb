## Why

The API-key collection handlers are registered only with a trailing slash even
though the dashboard contract and callers use both forms. Requests to
`/api/api-keys` therefore depend on redirect or fallback behavior instead of
reaching the collection operation directly.

## What Changes

- Make `GET /api/api-keys` and `GET /api/api-keys/` return the same collection
  response without a redirect.
- Make `POST /api/api-keys` and `POST /api/api-keys/` run the same creation
  operation without redirect-dependent request-body handling.
- Keep the trailing-slash routes canonical in OpenAPI and leave API-key detail
  routes and the global SPA fallback unchanged.
- Add request-level regression coverage for both collection route forms.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require equivalent direct handling for API-key collection routes
  with and without a trailing slash.

## Impact

- API surface: dashboard API-key collection `GET` and `POST` routes.
- Code: `app/modules/api_keys/api.py`.
- Tests: `tests/integration/test_api_keys_api.py`.
- No dependency, database, schema, frontend, or configuration changes.
