## MODIFIED Requirements

### Requirement: API keys can inspect and redeem reset credits within their account pool

The system SHALL expose `GET /v1/reset-credit` and `POST /v1/reset-credit` for API-key-authenticated self-service reset-credit access. Both routes MUST require a valid `Authorization: Bearer sk-clb-...` header even when `api_key_auth_enabled` is false globally. Validation failures MUST use the existing OpenAI error envelope used by `/v1/*` routes.

The target account pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only `assigned_account_ids` SHALL be eligible. If account scope is not enabled, all selectable accounts SHALL be eligible.

`GET /v1/reset-credit` SHALL return only credits for the authenticated key's eligible account pool. `POST /v1/reset-credit` SHALL reject requests whose `account_id` is outside that pool.

Before `POST /v1/reset-credit` decrypts and forwards the bearer token for the upstream consume call, the system SHALL refresh the target account with the normal account-token freshness rules and use the refreshed account credentials for the consume request.

If that self-service credential refresh fails, `POST /v1/reset-credit` SHALL stop before the upstream consume call, return a client-actionable conflict response, and keep using the existing `/v1/*` OpenAI error envelope.

On a successful `POST /v1/reset-credit` redemption, the system SHALL invalidate the redeemed account's cached reset-credit snapshot, force a usage refresh for that account, and invalidate account-selection cache state when that usage refresh writes updated usage. A failed or empty post-redeem usage refresh SHALL NOT roll back the successful credit redemption response.

#### Scenario: Self-service redemption refreshes stale account credentials before consume

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** the persisted access token for that account is stale but refreshable
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb refreshes the account before decrypting the consume bearer token
- **AND** the upstream reset-credit consume call uses the refreshed account credentials

#### Scenario: Self-service redemption surfaces refresh failures as conflicts

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** that account's credential refresh fails before the upstream consume call
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb returns a conflict response in the standard `/v1/*` OpenAI error envelope
- **AND** codex-lb does not call upstream reset-credit consume for that request
