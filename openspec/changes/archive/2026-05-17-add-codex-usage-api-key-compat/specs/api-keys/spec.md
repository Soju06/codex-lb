## MODIFIED Requirements

### Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the router-level API key auth guard scope, but the route SHALL accept valid LB API keys through a dedicated compatibility dependency.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: Codex usage accepts API key compatibility auth
- **WHEN** a request is made to `/api/codex/usage` with `Authorization: Bearer sk-clb-...`
- **AND** no `chatgpt-account-id` header is present
- **THEN** the route authenticates the request as an API-key caller
- **AND** returns a `RateLimitStatusPayload` compatibility response instead of `Missing chatgpt-account-id header`

#### Scenario: Codex usage preserves ChatGPT identity validation
- **WHEN** a request is made to `/api/codex/usage` with a ChatGPT bearer token and `chatgpt-account-id`
- **THEN** the route keeps using the ChatGPT identity validation path
- **AND** existing usage validation and error semantics remain unchanged
