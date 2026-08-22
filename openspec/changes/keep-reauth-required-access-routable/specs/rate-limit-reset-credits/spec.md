## MODIFIED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL start with the application lifespan when reset-credit polling is enabled. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas; `rate_limit_reset_credits_refresh_interval_seconds` is the operator control for total upstream load. The poll SHALL skip any account that is paused, deactivated, or lacks a usable `chatgpt-account-id`. A `reauth_required` account SHALL remain eligible with its stored access token and SHALL NOT proactively exchange its refresh token.

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
- **WHEN** an account is persisted as `paused` or `deactivated`
- **THEN** the scheduler performs no upstream reset-credits fetch for that account
- **AND** the cached snapshot for that account (if any) is left untouched by the skip

#### Scenario: Reauthentication warning account remains eligible
- **GIVEN** an account is `reauth_required` and has a usable `chatgpt-account-id`
- **WHEN** the scheduler polls reset credits
- **THEN** it calls upstream with the stored access token
- **AND** it does not proactively exchange refresh-token material

### Requirement: Reset credit snapshots are cached in memory keyed by account

The system SHALL store the most recent successful reset-credits response per account in an in-memory store keyed by account id. The store SHALL be concurrency-safe and SHALL provide an `invalidate(account_id)` operation. Account-summary mappers SHALL join the cached snapshot onto each account summary, exposing `available_reset_credits` (integer) and `reset_credit_nearest_expires_at` (ISO timestamp or null). Accounts with no cached snapshot SHALL expose `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`.

#### Scenario: Account summary reflects cached credits
- **GIVEN** an account has a cached reset-credits snapshot with `available_count: 2` and a soonest expiry of `2026-07-10T00:00:00Z`
- **WHEN** the account-summary mapper builds the summary for that account
- **THEN** the summary exposes `available_reset_credits: 2` and `reset_credit_nearest_expires_at: "2026-07-10T00:00:00Z"`

#### Scenario: Missing cache presents as zero credits
- **GIVEN** an account has no cached snapshot (e.g. immediately after restart)
- **WHEN** the account-summary mapper builds the summary for that account
- **THEN** the summary exposes `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`

#### Scenario: Invalidate forces re-fetch on next tick
- **WHEN** a caller invokes `invalidate(account_id)` for an account
- **THEN** subsequent reads for that account return no cached snapshot
- **AND** the next scheduler tick fetches a fresh snapshot from upstream

#### Scenario: In-flight refresh cannot restore an invalidated snapshot
- **GIVEN** a scheduler refresh starts fetching reset credits for an account
- **AND** another caller invokes `invalidate(account_id)` before that refresh stores its fetched response
- **WHEN** the refresh completes
- **THEN** the stale fetched response MUST NOT be written back into the cache

#### Scenario: Dashboard read invalidates stale snapshots for ineligible accounts
- **GIVEN** an account has a cached reset-credits snapshot
- **AND** the account is now persisted as `paused`, `deactivated`, or no longer has a usable `chatgpt-account-id`
- **WHEN** the dashboard invokes `GET /api/accounts/{id}/rate-limit-reset-credits`
- **THEN** the endpoint returns `null` without calling upstream
- **AND** the cached snapshot for that account is invalidated

#### Scenario: Dashboard read permits a reauthentication warning account
- **GIVEN** an account is `reauth_required` and has a usable `chatgpt-account-id`
- **WHEN** the dashboard invokes `GET /api/accounts/{id}/rate-limit-reset-credits`
- **THEN** the endpoint may fetch with the stored access token
- **AND** it does not reject the request solely because refresh-token exchange requires reauthentication
