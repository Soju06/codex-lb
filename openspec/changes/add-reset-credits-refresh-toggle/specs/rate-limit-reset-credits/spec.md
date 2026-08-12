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

The system SHALL expose setting `rate_limit_reset_credits_refresh_interval_seconds` (default `60`) to control the polling cadence. The system SHALL expose setting `rate_limit_reset_credits_refresh_enabled` (default `true`) to enable or disable background reset-credit polling.

#### Scenario: Operator tunes the polling interval
- **GIVEN** `rate_limit_reset_credits_refresh_interval_seconds` is set to `120`
- **WHEN** the application starts and runs
- **THEN** each eligible account's credits are fetched from upstream at most once per 120 seconds

#### Scenario: Operator disables background polling
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is set to `false`
- **WHEN** the application starts
- **THEN** the reset-credit polling scheduler does not create a background polling task
- **AND** no upstream reset-credits fetches occur
