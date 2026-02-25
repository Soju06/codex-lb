## ADDED Requirements

### Requirement: API key ownership

Each API key SHALL belong to exactly one dashboard user (`owner_user_id`).

#### Scenario: Create key as regular user

- **WHEN** an authenticated `user` creates an API key
- **THEN** the key is stored with `owner_user_id` equal to the caller's user id

#### Scenario: Admin lists keys for all users

- **WHEN** an authenticated `admin` requests API key list with owner filter
- **THEN** the system can return keys across users and apply the owner filter

#### Scenario: User lists keys

- **WHEN** an authenticated `user` requests API key list
- **THEN** only keys owned by that user are returned

### Requirement: Owner-scoped proxy account selection

When a proxy request is authenticated by API key, account selection for upstream proxying SHALL be constrained to accounts owned by the API key owner.

#### Scenario: Key cannot use another owner's accounts

- **WHEN** key owner has no active owned accounts but other users do
- **THEN** proxy request fails with existing `no_accounts` behavior

### Requirement: Mandatory API key for `/backend-api/codex/*`

All `/backend-api/codex/*` proxy endpoints SHALL require `Authorization: Bearer <api_key>` regardless of global `api_key_auth_enabled` setting.

#### Scenario: Missing key to codex backend route

- **WHEN** request is sent to `/backend-api/codex/responses` without bearer token
- **THEN** response is `401` with OpenAI-format auth error

#### Scenario: `/v1/*` behavior remains governed by global switch

- **WHEN** request is sent to `/v1/*`
- **THEN** API key requirement follows `api_key_auth_enabled` policy
