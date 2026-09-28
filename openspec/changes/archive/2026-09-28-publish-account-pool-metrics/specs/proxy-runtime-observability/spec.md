## ADDED Requirements

### Requirement: Account pool metrics describe inventory and baseline routing eligibility

When Prometheus support and metrics are enabled, the service MUST publish `codex_lb_accounts_total{status}` for every account status, including explicit zeroes, and a label-free `codex_lb_accounts_available` gauge. Accounts pending deletion MUST be excluded. Counts MUST derive from the committed account snapshot loaded by the existing routing-account cache refresh, without changing the cache's routing decisions. Each successful metrics scrape MUST refresh that snapshot before exposition; a failed refresh MUST return HTTP 503 instead of exposing an apparently current account snapshot.

Availability MUST use `account_eligibility.ROUTABLE_STATUSES` and `reauth_access_token_is_expired`: active accounts count, and reauthentication-required accounts count unless their access token has a known expiry at or before the observation time. Unknown or unreadable expiry MUST retain the existing eligibility behavior. Availability MUST NOT apply model, API-key, affinity, quota, cooldown, health-tier, or concurrency filters and MUST NOT claim that a particular request can be served.

In multiprocess mode these shared-pool gauges MUST expose the most recently published live-worker observation per series without a PID label or summation across workers. A newer zero MUST replace an older nonzero count. When Prometheus is absent, account-cache refresh MUST continue without metric publication or token decryption for metrics.

#### Scenario: Empty and changed inventory

- **GIVEN** an empty account pool
- **WHEN** metrics are scraped
- **THEN** all account-status series and availability are zero
- **WHEN** accounts are added, change status, become pending deletion, or are deleted
- **THEN** the next scrape reflects their committed state and clears obsolete nonzero status values without requiring proxy traffic

#### Scenario: Reauthentication token expires in a quiet pool

- **GIVEN** an active account and reauthentication-required accounts with future, expired, and unknown access-token expiry
- **WHEN** metrics are scraped
- **THEN** availability counts the active account and the reauthentication accounts with future or unknown expiry
- **WHEN** the future expiry is reached without a database mutation or proxy request
- **THEN** the next scrape excludes that account from availability while preserving its stored status count

#### Scenario: Replicated counts decrease

- **GIVEN** two live workers have published observations of the same account pool
- **WHEN** metrics are scraped with multiprocess aggregation
- **THEN** each account series uses the newest live observation without adding a PID label or summing duplicate inventory
- **AND** a newer empty-pool observation replaces an older populated observation

#### Scenario: Database refresh fails

- **GIVEN** the previous account snapshot is populated
- **WHEN** a metrics scrape cannot refresh it from the database
- **THEN** the scrape returns HTTP 503 without reporting stale or fabricated account counts as a successful observation

#### Scenario: Prometheus is not installed

- **GIVEN** the optional Prometheus client is absent
- **WHEN** the routing-account cache refreshes
- **THEN** routing-cache behavior is preserved and metric publication is a no-op
