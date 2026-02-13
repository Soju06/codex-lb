## CHANGED Requirements

### Requirement: API Key Bearer authentication middleware

The system SHALL validate API keys on proxy routes (`/v1/*`, `/backend-api/codex/*`) when `api_key_auth_enabled` is true. Validation MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

`/api/codex/usage` SHALL NOT be covered by API key middleware scope.

#### Scenario: API key middleware route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses` or `/backend-api/codex/responses`
- **THEN** API key middleware validation is applied

#### Scenario: Codex usage excluded from API key middleware scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key middleware validation is not applied
