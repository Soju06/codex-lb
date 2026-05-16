### MODIFIED Requirement: API Key authentication global switch
The system SHALL provide an `api_key_auth_enabled` boolean in `DashboardSettings`. When false (default), local requests to protected proxy routes MAY proceed without an API key, but non-local proxy requests MUST be rejected until proxy authentication is configured. When true, protected proxy routes require a valid API key via `Authorization: Bearer <key>`.

#### Scenario: Disable API key auth for a local proxy client
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a local client calls a protected proxy route
- **THEN** the request proceeds without API key authentication

#### Scenario: Disable API key auth for a non-local proxy client
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a non-local client calls a protected proxy route
- **THEN** the request is rejected with 401 until proxy authentication is configured

### MODIFIED Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: API key auth disabled returns None for local requests
- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as local
- **THEN** the dependency returns `None` and the request proceeds without authentication

#### Scenario: API key auth disabled rejects non-local requests
- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as non-local
- **THEN** the dependency rejects the request with 401
