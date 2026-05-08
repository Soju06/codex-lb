## MODIFIED Requirements

### Requirement: API Key creation
The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), `expires_at` (optional ISO 8601 datetime), and `peer_fallback_base_urls` (optional list of peer codex-lb base URLs). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

#### Scenario: Create key with timezone-aware expiration
- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

#### Scenario: Create key with peer fallback URLs
- **WHEN** admin submits `POST /api/api-keys` with a list of `peerFallbackBaseUrls`
- **THEN** the system validates and persists those peer fallback URLs for the new key
- **AND** the response includes the normalized `peerFallbackBaseUrls`

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `assignedAccountIds`, and `peerFallbackBaseUrls`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

#### Scenario: Update non-existent key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

#### Scenario: Update peer fallback URLs
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `peerFallbackBaseUrls`
- **THEN** the system replaces that key's peer fallback URLs with the submitted URLs
- **AND** the response includes the normalized `peerFallbackBaseUrls`

#### Scenario: Reject invalid peer fallback URLs
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an invalid peer fallback base URL
- **THEN** the system rejects the update with a dashboard validation error

### Requirement: API Key Bearer authentication guard

The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`. The typed `ApiKeyData` MUST include the key's peer fallback base URLs.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: API key guard route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses`, `/backend-api/codex/responses`, `/v1/audio/transcriptions`, or `/backend-api/transcribe`
- **THEN** the API key guard validation is applied

#### Scenario: Codex usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key guard validation is not applied

#### Scenario: Valid API key injected into handler

- **WHEN** `api_key_auth_enabled` is true and a valid Bearer token is provided
- **THEN** the route handler receives a typed `ApiKeyData` parameter (not `request.state`)

#### Scenario: Valid API key carries peer fallback URLs
- **WHEN** `api_key_auth_enabled` is true and a valid Bearer token is provided for a key with peer fallback URLs
- **THEN** the route handler receives `ApiKeyData` containing those peer fallback base URLs

#### Scenario: API key auth disabled returns None for local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as local
- **THEN** the dependency returns `None` and the request proceeds without authentication

#### Scenario: API key auth disabled rejects non-local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as non-local
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401

#### Scenario: Disabled auth allowlist uses raw socket peer only

- **WHEN** `api_key_auth_enabled` is false
- **AND** forwarded headers claim a different client IP
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401
- **AND** forwarded headers do not satisfy the explicit allowlist
