## MODIFIED Requirements

### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowedModels` (optional list), `weeklyTokenLimit` (optional integer), `expiresAt` (optional ISO 8601 datetime), `assignedAccountIds` (optional list), and `usageSections` (optional comma-separated string, defaults to `"upstream_limits,account_pool_usage"`). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

When `assignedAccountIds` is omitted or empty, the created key SHALL remain unscoped and apply to all accounts. When `assignedAccountIds` is provided with one or more valid account IDs, the created key SHALL enable account-assignment scope and persist those assignments.

#### Scenario: Create unscoped key without assigned accounts

- **WHEN** admin submits `POST /api/api-keys` without `assignedAccountIds`
- **THEN** the created key returns `accountAssignmentScopeEnabled = false`
- **AND** `assignedAccountIds = []`

#### Scenario: Create scoped key with assigned accounts

- **WHEN** admin submits `POST /api/api-keys` with `assignedAccountIds` containing valid account IDs
- **THEN** the created key returns `accountAssignmentScopeEnabled = true`
- **AND** `assignedAccountIds` matches the supplied accounts

#### Scenario: Reject unknown assigned account IDs on create

- **WHEN** admin submits `POST /api/api-keys` with an unknown account ID in `assignedAccountIds`
- **THEN** the system returns 400

#### Scenario: Create key and show plain key

- **WHEN** admin submits `POST /api/api-keys` with a valid payload
- **THEN** the response contains a key matching `sk-clb-[0-9a-f]{48}`
- **AND** the full plain key is returned exactly once
- **AND** the system never returns the plain key on subsequent reads

#### Scenario: Create key with timezone-aware expiration

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

### Requirement: API Key regeneration

The system SHALL allow regenerating an API key via `POST /api/api-keys/{id}/regenerate`. This MUST generate a new key matching `sk-clb-[0-9a-f]{48}` with a new hash and prefix while preserving all other properties (name, models, limits, expiration). The new plain key MUST be returned exactly once.

#### Scenario: Regenerate key

- **WHEN** admin calls `POST /api/api-keys/{id}/regenerate`
- **THEN** the system returns the updated key object with a new `key` and `keyPrefix`
- **AND** the new key matches `sk-clb-[0-9a-f]{48}`
- **AND** the old key immediately stops authenticating

## ADDED Requirements

### Requirement: Previously issued API key compatibility

The system MUST continue authenticating an already-issued API key by its stored
SHA-256 hash regardless of whether its plaintext suffix uses the current
48-character hexadecimal format or the earlier 43-character base64url format.

#### Scenario: Authenticate an already-issued base64url key

- **GIVEN** an API key created before the generated-key format correction has a stored hash
- **WHEN** the client authenticates with that unchanged plaintext key
- **THEN** the system authenticates it through the existing hash lookup
- **AND** the system does not require key rotation or data migration
