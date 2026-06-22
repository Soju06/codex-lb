## ADDED Requirements

### Requirement: API keys can inspect and redeem reset credits within their account pool

The system SHALL expose `GET /v1/reset-credit` and `POST /v1/reset-credit` for API-key-authenticated self-service reset-credit access. Both routes MUST require a valid `Authorization: Bearer sk-clb-...` header even when `api_key_auth_enabled` is false globally. Validation failures MUST use the existing OpenAI error envelope used by `/v1/*` routes.

The target account pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only `assigned_account_ids` SHALL be eligible. If account scope is not enabled, all selectable accounts SHALL be eligible.

`GET /v1/reset-credit` SHALL return only credits for the authenticated key's eligible account pool. `POST /v1/reset-credit` SHALL reject requests whose `account_id` is outside that pool.

On a successful `POST /v1/reset-credit` redemption, the system SHALL invalidate the redeemed account's cached reset-credit snapshot, force a usage refresh for that account, and invalidate account-selection cache state when that usage refresh writes updated usage. A failed or empty post-redeem usage refresh SHALL NOT roll back the successful credit redemption response.

#### Scenario: Missing API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` without a Bearer token
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Invalid API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with an unknown, expired, or inactive Bearer key
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Scoped API key sees only assigned accounts

- **WHEN** an API key has account scope enabled with assigned accounts
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response includes reset-credit entries only for those assigned accounts

#### Scenario: Unscoped API key can read the full selectable pool

- **WHEN** an API key has account scope disabled
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response may include reset-credit entries for any selectable account that currently has an available cached credit

#### Scenario: Out-of-pool account is rejected on redeem

- **WHEN** a client calls `POST /v1/reset-credit` with an `account_id` outside the authenticated API key's eligible pool
- **THEN** the system returns 403 without redeeming any credit

#### Scenario: Self-service reset-credit works while global proxy auth is disabled

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with a valid Bearer key
- **THEN** the system still authenticates that key and applies the same account-pool rules

#### Scenario: Successful self-service redemption refreshes usage for immediate follow-up traffic

- **GIVEN** an eligible account has a redeemable reset credit and persisted usage/account state that still reflects a blocked window
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** the redeemed account's cached reset-credit snapshot is invalidated
- **AND** codex-lb forces a usage refresh for that account before returning
- **AND** any account-selection cache entry derived from the stale usage state is invalidated when the refresh writes updated usage
- **AND** the response still returns the upstream `{code, windows_reset, redeemed_at}` success payload
