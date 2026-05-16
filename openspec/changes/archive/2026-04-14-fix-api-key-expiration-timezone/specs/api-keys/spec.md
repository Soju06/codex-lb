### MODIFIED Requirement: API Key creation
The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), and `expires_at` (optional ISO 8601 datetime). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

#### Scenario: Create key with timezone-aware expiration
- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

### MODIFIED Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant
