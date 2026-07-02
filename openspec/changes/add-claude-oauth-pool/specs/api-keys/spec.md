# api-keys Specification (delta)

## MODIFIED Requirements

### Requirement: API Key creation accepts provider_scope

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with an optional `provider_scope` field. On the JSON wire, the field SHALL be a JSON array of strings, each a member of `{'codex', 'claude'}`. The server SHALL deduplicate entries, sort them in ascending order, and reject empty arrays with HTTP 400. Values outside the allowed set SHALL be rejected with HTTP 400. When `provider_scope` is omitted, the system SHALL default it to `['codex']`. Internally the values are persisted as a CSV string in `api_keys.provider_scope`; the JSON array <-> CSV round-trip MUST be lossless.

#### Scenario: Create key with provider_scope=['claude']

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "claude-only", "providerScope": ["claude"] }`
- **THEN** the created key returns `providerScope: ["claude"]`

#### Scenario: Create key with provider_scope=['codex','claude']

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "both", "providerScope": ["codex","claude"] }`
- **THEN** the created key returns `providerScope: ["codex","claude"]`

#### Scenario: Create key without provider_scope defaults to ['codex']

- **WHEN** admin submits `POST /api/api-keys` without `providerScope`
- **THEN** the created key returns `providerScope: ["codex"]`

#### Scenario: Reject unknown provider_scope value

- **WHEN** admin submits `POST /api/api-keys` with `providerScope: ["openai","claude"]`
- **THEN** the system returns 400

#### Scenario: Deduplicated and sorted values are accepted

- **WHEN** admin submits `POST /api/api-keys` with `providerScope: ["claude","codex","claude"]`
- **THEN** the created key returns `providerScope: ["claude","codex"]` (deduplicated and sorted)

#### Scenario: Empty provider_scope is rejected

- **WHEN** admin submits `POST /api/api-keys` with `providerScope: []`
- **THEN** the system returns 400

### Requirement: API Key update accepts provider_scope

The system SHALL allow updating `provider_scope` via `PATCH /api/api-keys/{id}`. On the JSON wire, the new value SHALL be a JSON array of strings, each a member of `{'codex', 'claude'}`. The server SHALL deduplicate entries, sort them in ascending order, and reject empty arrays with HTTP 400. Other key properties (`name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`) SHALL be unaffected.

#### Scenario: Update provider_scope to ['claude']

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "providerScope": ["claude"] }`
- **THEN** the key returns `providerScope: ["claude"]`
- **AND** all other properties are unchanged

#### Scenario: Reject invalid provider_scope on update

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "providerScope": ["openai"] }`
- **THEN** the system returns 400

### Requirement: API Key response includes provider_scope

`ApiKeyResponse` SHALL include `provider_scope` as a JSON array of strings (deduplicated, sorted, non-empty). Internally this maps to a CSV column; the on-the-wire shape is the JSON array.

#### Scenario: List keys returns provider_scope

- **WHEN** admin calls `GET /api/api-keys`
- **THEN** each key entry includes `providerScope` as a JSON array

### Requirement: API key is rejected on a route whose provider is not in its provider_scope

When a request arrives at a proxy route tagged with a provider (`/v1/*` is `codex`, `/claude/v1/*` is `claude`), the system SHALL reject the request with HTTP 403 if the authenticated API key's `provider_scope` does not include that provider.

#### Scenario: Codex-only key is rejected on /claude/v1/*

- **GIVEN** an API key with `providerScope: ["codex"]`
- **WHEN** a client calls `POST /claude/v1/messages` with that key
- **THEN** the system returns 403

#### Scenario: Claude-only key is rejected on /v1/*

- **GIVEN** an API key with `providerScope: ["claude"]`
- **WHEN** a client calls `POST /v1/chat/completions` with that key
- **THEN** the system returns 403

#### Scenario: Both-providers key succeeds on either route

- **GIVEN** an API key with `providerScope: ["codex","claude"]`
- **WHEN** a client calls `POST /claude/v1/messages` with that key
- **THEN** the request is processed normally

### Requirement: Frontend API Key management exposes provider_scope

The SPA API key create and edit dialogs SHALL include a `providerScope` field that lets the admin pick `codex`, `claude`, or both. The form MUST validate that at least one provider is selected before allowing submission.

#### Scenario: Create key dialog shows provider multi-select

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows a "Provider scope" multi-select including `codex` and `claude`
- **AND** at least one option is selected by default (`codex`)

#### Scenario: Edit key dialog preserves current provider_scope

- **WHEN** an admin opens the edit dialog for a key with `providerScope: ["claude"]`
- **THEN** `codex` is not selected and `claude` is selected
- **AND** submitting saves the unchanged selection