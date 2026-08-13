## ADDED Requirements

### Requirement: Per-account weekly usage cap

The proxy account selector SHALL support an optional per-account weekly usage cap, persisted as `Account.weekly_usage_cap_pct` (nullable float, `NULL` = no cap). When the cap is set and the account's resolved secondary (weekly) window `used_percent` is greater than or equal to the cap, the account SHALL be excluded from candidate states before any routing strategy, health-tier, or sticky-session logic runs. Accounts without usage data SHALL remain eligible. Accounts whose cap is `NULL` SHALL route exactly as before. Exclusion SHALL lift automatically when the account's resolved weekly usage falls below the cap (for example after a window reset).

#### Scenario: Capped account is excluded from routing

- **GIVEN** an account with `weekly_usage_cap_pct = 20`
- **AND** its latest secondary-window usage is 20% or higher
- **WHEN** any request selects an account
- **THEN** the capped account is not a routing candidate
- **AND** an uncapped eligible account serves the request instead

#### Scenario: Account below its cap routes normally

- **GIVEN** an account with `weekly_usage_cap_pct = 20`
- **AND** its latest secondary-window usage is below 20%
- **WHEN** any request selects an account
- **THEN** the account remains eligible under all existing gates and strategies

#### Scenario: Missing usage data fails open

- **GIVEN** an account with `weekly_usage_cap_pct = 20`
- **AND** no secondary-window usage row has been recorded yet
- **WHEN** any request selects an account
- **THEN** the account remains eligible

#### Scenario: Sticky session rebinds when the pinned account hits its cap

- **GIVEN** a sticky session pinned to an account
- **WHEN** the pinned account's weekly usage reaches its cap
- **THEN** the pinned account is absent from candidate states
- **AND** the existing sticky fallback rebinds the session to an eligible account

#### Scenario: All accounts capped yields the standard no-account error

- **GIVEN** every account has reached its weekly usage cap
- **WHEN** any request selects an account
- **THEN** selection returns the existing "No available accounts" error instead of exceeding any cap

### Requirement: Weekly usage cap management contract

The dashboard accounts API SHALL expose `weekly_usage_cap_pct` on every account summary and SHALL provide `PUT /api/accounts/{account_id}/weekly-usage-cap`, guarded by the existing dashboard-session write dependency, to set or clear the cap. The endpoint SHALL accept a JSON body `{"cap": <number>}` with `0 <= cap <= 100`, or `{"cap": null}` to clear the cap. Unknown account ids SHALL return 404 with error code `account_not_found`. Changes SHALL invalidate the account-selection cache so routing observes the new cap without waiting for the cache TTL. The dashboard accounts UI SHALL let an operator set or clear the cap from the account detail panel.

#### Scenario: Setting a cap persists and appears on the summary

- **WHEN** an authenticated dashboard session calls `PUT /api/accounts/{account_id}/weekly-usage-cap` with `{"cap": 20}`
- **THEN** the response is 200 with the stored value
- **AND** subsequent `GET /api/accounts` includes `weekly_usage_cap_pct: 20` on that account

#### Scenario: Clearing a cap restores uncapped routing

- **WHEN** an authenticated dashboard session calls `PUT /api/accounts/{account_id}/weekly-usage-cap` with `{"cap": null}`
- **THEN** the stored cap is cleared
- **AND** the account routes according to the existing gates only

#### Scenario: Out-of-range cap is rejected

- **WHEN** the endpoint is called with `{"cap": 120}` or a negative value
- **THEN** the response is a 4xx validation error and the stored cap is unchanged

#### Scenario: Setting a cap on an unknown account returns 404

- **WHEN** the endpoint is called with an `account_id` that does not exist
- **THEN** the response is 404 with error code `account_not_found`
