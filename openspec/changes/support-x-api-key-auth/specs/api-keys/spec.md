## MODIFIED Requirements

### Requirement: API Key authentication global switch
The system SHALL provide an `api_key_auth_enabled` boolean in `DashboardSettings`. When false (default), local requests to protected proxy routes MAY proceed without an API key. Operators MAY additionally opt specific non-local proxy clients into unauthenticated access by configuring `proxy_unauthenticated_client_cidrs`. Requests that are neither local nor explicitly allowlisted MUST be rejected until proxy authentication is configured. When true, protected proxy routes require a valid codex-lb API key in either the `Authorization` header using the Bearer authentication scheme or the `x-api-key` header.

#### Scenario: Enable API key auth

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": true }`
- **THEN** subsequent proxy requests without a valid Bearer token or `x-api-key` are rejected with 401

### Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the supplied codex-lb API key and look up the hash in the `api_keys` table.

The dependency SHALL accept a valid codex-lb API key from either `Authorization: Bearer <key>` or `x-api-key: <key>`. When both are present, the dependency SHALL try the Authorization value first and SHALL fall back to `x-api-key` only when the Authorization value is missing, malformed, or invalid. This fallback applies only to codex-lb API key authentication; ChatGPT caller-identity validation remains Bearer-only.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: Valid x-api-key is injected into handler

- **WHEN** `api_key_auth_enabled` is true and a valid `x-api-key` header is provided
- **THEN** the route handler receives a typed `ApiKeyData` parameter

#### Scenario: Invalid Authorization falls back to valid x-api-key

- **WHEN** `api_key_auth_enabled` is true
- **AND** `Authorization` is missing, malformed, or contains an invalid API key
- **AND** `x-api-key` contains a valid codex-lb API key
- **THEN** the dependency authenticates the request with `x-api-key`

### Requirement: Self-service API key usage lookup accepts x-api-key
The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid API key in either `Authorization: Bearer sk-clb-...` or `x-api-key: sk-clb-...` even when `api_key_auth_enabled` is false globally.

#### Scenario: Self-service usage lookup accepts x-api-key

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/usage` with a valid `x-api-key`
- **THEN** the route returns usage for that authenticated key
