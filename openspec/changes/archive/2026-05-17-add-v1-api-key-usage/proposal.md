## Why

codex-lb API keys can authenticate `/v1/*` requests, but there is no matching self-service usage endpoint that a client can call with the same Bearer key. Current usage surfaces either require ChatGPT OAuth plus `chatgpt-account-id` (`/api/codex/usage`) or a dashboard session cookie (`/api/usage/*`, `/api/api-keys*`). That makes API-key-only clients unable to introspect their own usage and limit state.

## What Changes

- Add `GET /v1/usage` for API-key-authenticated self-usage lookup.
- Require a valid Bearer API key for this endpoint even when the global `api_key_auth_enabled` switch is off.
- Return usage totals and current limit state for the authenticated key only.

## Capabilities

### Modified Capabilities

- `api-keys`
