## ADDED Requirements

### Requirement: Operators can apply a banked upstream usage reset for one account

The dashboard MUST expose an admin-only endpoint that consumes one banked rate-limit reset credit for a single account via upstream `POST /wham/rate-limit-reset-credits/consume`, then immediately refreshes that account's `/wham/usage` snapshot. The endpoint MUST surface before/after usage, status, and available reset credit count.

#### Scenario: Apply reset consumes credit and refreshes usage
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/usage-reset/apply`
- **AND** the account is `active`, `rate_limited`, or `quota_exceeded`
- **AND** the latest primary usage row reports `rate_limit_reset_available_count > 0`
- **THEN** the service POSTs to upstream consume with a fresh `redeem_request_id`
- **AND** the service triggers `UsageUpdater.force_refresh` for that account
- **AND** the response carries before/after usage percents, account status, and available reset counts

#### Scenario: Apply reset rejects hard-blocked accounts
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/usage-reset/apply`
- **AND** the account `status` is `paused`, `deactivated`, or `reauth_required`
- **THEN** the endpoint responds `409` with code `account_not_reset_applicable`
- **AND** no upstream consume request is sent

#### Scenario: Apply reset rejects accounts with no banked credit
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/usage-reset/apply`
- **AND** the latest primary usage row reports `rate_limit_reset_available_count` is `null` or `0`
- **THEN** the endpoint responds `409` with code `account_usage_reset_no_credit`
- **AND** no upstream consume request is sent

#### Scenario: Dashboard exposes Apply reset only when a credit is available
- **WHEN** the dashboard renders account actions
- **AND** `rate_limit_reset_available_count > 0`
- **AND** the account is not `paused`, `deactivated`, or `reauth_required`
- **THEN** the dashboard exposes an Apply reset action
- **AND** confirming the action shows a warning that one upstream reset credit will be consumed
- **BUT WHEN** `rate_limit_reset_available_count` is `null` or `0`
- **THEN** the Apply reset action is disabled