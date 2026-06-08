## Why

Operators and client integrations need a stable way to identify which codex-lb build produced a successful or client-error HTTP response. Today the service exposes request identifiers and some route-specific headers, but it does not expose the running application version on normal HTTP responses.

Adding a version header improves debugging and rollout verification without changing any response body contracts. The header should be present on successful and client-error HTTP responses, but it should not be attached to 5xx responses where the server did not complete the request successfully.

## What Changes

- Add a global HTTP response metadata contract that returns `X-App-Version` on HTTP responses with status codes in the `200-499` range.
- Define the header value as the running codex-lb package version rather than the stale FastAPI metadata version.
- Exclude the header from `5xx` responses and websocket traffic.
- Add regression coverage for success, client-error, and server-error response paths.

## Capabilities

### New Capabilities

- `api-response-metadata`: global HTTP response headers that codex-lb adds across route families.

### Modified Capabilities

- None.

## Impact

- **Code**: `app/main.py`, `app/core/middleware/*`
- **Tests**: focused middleware unit coverage plus integration coverage on representative `2xx`, `4xx`, and `5xx` routes
- **Behavior**: HTTP `200-499` responses now include `X-App-Version` with the running codex-lb package version
