# account-pool-usage-v1-usage Specification

## Purpose
TBD - created by archiving change add-account-pool-usage-to-v1-usage. Update Purpose after archive.
## Requirements
### Requirement: /v1/usage response includes account_pool_usage

The system SHALL include an `account_pool_usage` object in the `GET /v1/usage` response containing pooled account remaining capacity percentages for the primary (5h) and secondary (7d/weekly) windows, when the API key's `usage_sections` setting includes `account_pool_usage` and the quota privacy toggle does not hide upstream quota data.

The `account_pool_usage` object SHALL contain:
- `primary` (float): the remaining percent of primary (5h) pooled account capacity, or `null` if no primary capacity exists
- `secondary` (float): the remaining percent of secondary (7d) pooled account capacity, or `null` if no secondary capacity exists

The computation SHALL use the same account scope as the API key's assigned accounts. When no accounts are assigned, all active (non-DEACTIVATED, non-PAUSED) accounts SHALL be considered. Computation SHALL mirror the existing `PooledCreditData` computation used for the dashboard API key list.

#### Scenario: Account pool usage with assigned accounts

- **WHEN** a valid API key with assigned accounts calls `GET /v1/usage`
- **AND** the API key has `usage_sections` containing `account_pool_usage`
- **AND** pooled account capacity has remaining percentages of 75.0 (primary) and 90.0 (secondary)
- **THEN** the response includes `account_pool_usage: { primary: 75.0, secondary: 90.0 }`

#### Scenario: Account pool usage when no primary capacity

- **WHEN** a valid API key calls `GET /v1/usage`
- **AND** pooled primary capacity is 0
- **THEN** `account_pool_usage.primary` is `null`

#### Scenario: Account pool usage when the enabled pool has no active capacity-bearing accounts

- **WHEN** a valid API key calls `GET /v1/usage`
- **AND** the API key has `usage_sections` containing `account_pool_usage`
- **AND** the scoped pool has no active or capacity-bearing accounts
- **THEN** the response includes `account_pool_usage: { primary: null, secondary: null }`

#### Scenario: Account pool usage excluded by usage_sections

- **WHEN** a valid API key calls `GET /v1/usage`
- **AND** the API key's `usage_sections` does NOT include `account_pool_usage`
- **THEN** `account_pool_usage` is `null`

#### Scenario: Account pool usage excluded by quota privacy toggle

- **WHEN** a valid API key calls `GET /v1/usage`
- **AND** the API key's `usage_sections` includes `account_pool_usage`
- **AND** `hide_upstream_quota_from_api_keys` is enabled
- **THEN** `account_pool_usage` is `null`

### Requirement: API key usage_sections controls which /v1/usage detail sections are returned

The system SHALL store an API key's visible usage sections in a `usage_sections` TEXT field as a comma-separated list. The supported values SHALL be `upstream_limits` and `account_pool_usage`. The system SHALL parse this field when building the `/v1/usage` response and conditionally include the corresponding sections, subject to global quota privacy settings.

#### Scenario: Default usage_sections includes all sections

- **WHEN** an API key is created without specifying `usage_sections`
- **THEN** the key's `usage_sections` SHALL be `"upstream_limits,account_pool_usage"`
- **AND** `/v1/usage` response includes both `upstream_limits` and `account_pool_usage`

#### Scenario: upstream_limits excluded

- **WHEN** an API key has `usage_sections` set to `"account_pool_usage"`
- **AND** a client calls `GET /v1/usage`
- **THEN** the response includes `account_pool_usage` but `upstream_limits` is an empty list

#### Scenario: account_pool_usage excluded

- **WHEN** an API key has `usage_sections` set to `"upstream_limits"`
- **AND** a client calls `GET /v1/usage`
- **THEN** the response includes `upstream_limits` and `account_pool_usage` is `null`

#### Scenario: Empty usage_sections excludes both

- **WHEN** an API key has `usage_sections` set to `""`
- **AND** a client calls `GET /v1/usage`
- **THEN** `upstream_limits` is an empty list AND `account_pool_usage` is `null`

