## ADDED Requirements

### Requirement: Reset credits refresh every 60-second scheduler interval

The existing background usage refresh loop MUST fetch `GET /wham/rate-limit-reset-credits` for each refreshable account every 60 seconds.

Each fetch MUST be authenticated with that account's bearer token and MUST include the `chatgpt-account-id` header for the same account.

#### Scenario: New upstream credit is inserted once

- **GIVEN** an account is eligible for background refresh
- **AND** upstream returns a credit whose `(account_id, credit_id)` pair is not stored
- **WHEN** the scheduler refresh runs
- **THEN** the system stores one new row for that credit
- **AND** subsequent refreshes do not duplicate it

#### Scenario: Refresh fetch uses account-scoped authentication

- **GIVEN** an account is eligible for background refresh
- **WHEN** the scheduler requests `GET /wham/rate-limit-reset-credits` for that account
- **THEN** the request uses that account's bearer token for `Authorization`
- **AND** the request includes that account's `chatgpt-account-id` header

### Requirement: Stored credits expire locally

Stored reset-credit rows MUST transition to `expired` when `now > expires_at`.

Transient reset-credit fetch failures MUST NOT clear previously stored credits or make UI-visible available reset counts drop to zero solely because the fetch failed.

#### Scenario: Expired stored credit stops counting as available

- **GIVEN** a stored credit row has `status = "available"`
- **AND** its `expires_at` is earlier than the current time
- **WHEN** the refresh iteration normalizes stored rows
- **THEN** the row status becomes `expired`
- **AND** it is excluded from the available reset count

### Requirement: Operator can redeem a rate-limit reset credit

The system SHALL provide an operator-triggered endpoint that redeems one rate-limit reset credit for a given account by sending `POST /wham/rate-limit-reset-credits/consume` to the upstream API.

The system SHALL select the available credit with the nearest `expires_at` for redemption.

The system SHALL generate a client-side `redeem_request_id` (UUID v4) for each consume request.

On a successful consume response, the system SHALL mark the redeemed credit's status as `redeemed` in the local database and invalidate cached account state.

#### Scenario: Operator triggers reset from account detail

- **GIVEN** an account has at least one available rate-limit reset credit
- **WHEN** the operator clicks the Reset button in the account detail panel
- **THEN** a confirmation dialog appears
- **AND** on confirm, the system selects the nearest-expiry available credit
- **AND** sends the consume request to the upstream API
- **AND** marks the credit as redeemed on success

#### Scenario: No available credits

- **GIVEN** an account has zero available rate-limit reset credits
- **WHEN** the operator attempts to trigger a reset
- **THEN** the request fails with a clear error

#### Scenario: Operator triggers reset from dashboard

- **GIVEN** a dashboard account card or list row shows a Reset button
- **WHEN** the operator clicks it
- **THEN** a confirmation dialog appears
- **AND** on confirm, the same consume flow executes

#### Scenario: Transient fetch failure preserves stored credits

- **GIVEN** an account has one or more stored reset-credit rows that still count as available
- **AND** a later `GET /wham/rate-limit-reset-credits` attempt for that account fails transiently
- **WHEN** the refresh iteration completes
- **THEN** the previously stored credit rows remain stored
- **AND** the available reset count derived from stored rows does not drop to zero solely because that fetch failed
