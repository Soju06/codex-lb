## ADDED Requirements

### Requirement: Usage refresh persists banked reset credit availability

When `UsageUpdater` refreshes an account from `GET /wham/usage`, it MUST parse `rate_limit_reset_credits.available_count` when present and persist the value on the primary `usage_history` row written for that refresh tick. When the field is absent, the stored value MUST be `null`.

#### Scenario: Usage payload includes banked reset credits
- **WHEN** `/wham/usage` returns `rate_limit_reset_credits.available_count: 1`
- **AND** the updater writes a primary usage history row
- **THEN** that row stores `rate_limit_reset_available_count: 1`

#### Scenario: Usage payload omits banked reset credits
- **WHEN** `/wham/usage` omits `rate_limit_reset_credits`
- **AND** the updater writes a primary usage history row
- **THEN** that row stores `rate_limit_reset_available_count: null`