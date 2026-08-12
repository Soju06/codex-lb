## MODIFIED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL start with the application lifespan when reset-credit polling is enabled. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas; `rate_limit_reset_credits_refresh_interval_seconds` is the operator control for total upstream load. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts with default settings
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds plus the jitter bound

#### Scenario: Every replica refreshes its local cache
- **WHEN** the application is deployed with multiple running replicas
- **THEN** each replica refreshes its own in-memory reset-credit snapshots on the configured cadence
- **AND** dashboard reads served by any replica can observe populated reset-credit data after that replica's refresh tick

#### Scenario: Two replicas do not fetch in lockstep
- **GIVEN** two replicas start with identical configuration
- **WHEN** their refresh loops run
- **THEN** their startup delays are independent uniform draws over the full interval and each tick interval carries independent +/-10% jitter, so the replicas' tick times are not synchronized

#### Scenario: Ineligible accounts are skipped
- **WHEN** an account is persisted as `paused`, `reauth_required`, or `deactivated`
- **THEN** the scheduler performs no upstream reset-credits fetch for that account
- **AND** the cached snapshot for that account (if any) is left untouched by the skip

### Requirement: Reset credit polling interval is configurable

The system SHALL expose setting `rate_limit_reset_credits_refresh_interval_seconds` (default `60`) to control the polling cadence. The system SHALL expose setting `rate_limit_reset_credits_refresh_enabled` (default `true`) to enable or disable background reset-credit polling. Because the refresh loop is the sole driver of automatic reset-credit redemption, disabling background polling SHALL also disable automatic redemption; when polling is disabled while the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL log a configuration-conflict warning at startup naming both settings. While polling is disabled, the dashboard settings update SHALL reject a request that newly enables `auto_redeem_reset_credits_before_expiry` with a bad-request error naming the polling toggle; an already-persisted opt-in SHALL remain readable and re-savable so unrelated settings edits are not blocked.

#### Scenario: Operator tunes the polling interval
- **GIVEN** `rate_limit_reset_credits_refresh_interval_seconds` is set to `120`
- **WHEN** the application starts and runs
- **THEN** each eligible account's credits are fetched from upstream at most once per 120 seconds

#### Scenario: Operator disables background polling
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **WHEN** the application starts
- **THEN** the reset-credit polling scheduler does not create a background polling task
- **AND** no upstream reset-credits fetches occur

#### Scenario: Disabled polling conflicts with persisted auto-redeem opt-in
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `true`
- **WHEN** the application starts
- **THEN** the system logs a configuration-conflict warning naming both settings
- **AND** no automatic reset-credit redemption occurs while polling remains disabled

#### Scenario: Auto-redeem opt-in is rejected while polling is disabled
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is `false`
- **WHEN** a dashboard settings update sets `auto_redeem_reset_credits_before_expiry` to `true`
- **THEN** the update is rejected with a bad-request error naming the polling toggle
- **AND** the persisted setting remains `false`

#### Scenario: Persisted auto-redeem does not block unrelated settings edits
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **AND** the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is already `true`
- **WHEN** a full settings payload that keeps the opt-in unchanged is submitted
- **THEN** the update succeeds
