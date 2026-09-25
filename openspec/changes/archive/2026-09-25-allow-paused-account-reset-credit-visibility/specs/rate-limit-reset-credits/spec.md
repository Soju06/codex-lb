## MODIFIED Requirements

### Requirement: Reset credit snapshots are cached in memory keyed by account

The system SHALL store the most recent successful reset-credits response per account in an in-memory store keyed by account id. The store SHALL be concurrency-safe and SHALL provide an `invalidate(account_id)` operation. Account-summary mappers SHALL join the cached snapshot onto each account summary, exposing `available_reset_credits` (integer) and `reset_credit_nearest_expires_at` (ISO timestamp or null). Accounts with no cached snapshot SHALL expose `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`.

#### Scenario: Account summary reflects cached credits
- **GIVEN** an account has a cached reset-credits snapshot with `available_count: 2` and a soonest expiry of `2026-07-10T00:00:00Z`
- **WHEN** the account-summary mapper builds the summary for that account
- **THEN** the summary exposes `available_reset_credits: 2` and `reset_credit_nearest_expires_at: "2026-07-10T00:00:00Z"`

#### Scenario: Missing cache presents as zero credits
- **GIVEN** an account has no cached reset-credits snapshot (e.g. immediately after restart)
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
- **AND** the account is now persisted as `reauth_required`, `deactivated`, or no longer has a usable `chatgpt-account-id`
- **WHEN** the dashboard invokes `GET /api/accounts/{id}/rate-limit-reset-credits`
- **THEN** the endpoint returns `null` without calling upstream
- **AND** the cached snapshot for that account is invalidated

#### Scenario: Paused account retains last observed reset credits
- **GIVEN** a paused account has a usable ChatGPT account identity and a cached snapshot
- **WHEN** the dashboard reads the cached endpoint or account summary
- **THEN** the system SHALL return its cached count and expiry without invalidating it merely because the account is paused
- **AND** the read SHALL NOT initiate upstream polling or redemption

## ADDED Requirements

### Requirement: Paused dashboard credit observation is independent of redemption

The dashboard `GET /api/accounts/{account_id}/usage-reset-credits` SHALL allow an authorized account read for a paused account with usable credentials and ChatGPT account identity. It SHALL reuse existing credential refresh, upstream-401 retry, account visibility, and account-bound proxy routing controls. A successful read MUST NOT reactivate the account or consume a credit. Failed reads SHALL retain the existing dashboard error envelope rather than return a successful zero count. Deactivated account reads MUST remain rejected. Paused accounts MUST remain excluded from background reset-credit polling and manual and automatic redemption.

#### Scenario: Inspect paused account without resuming it
- **GIVEN** a paused account has usable credentials and a permitted upstream route
- **WHEN** an authorized dashboard user requests its usage reset-credit count
- **THEN** the system SHALL return the upstream available count
- **AND** the account SHALL remain paused and no credit SHALL be consumed

#### Scenario: Read failure does not masquerade as no credits
- **GIVEN** a paused account's upstream usage request fails
- **WHEN** the dashboard requests its reset-credit count
- **THEN** the endpoint SHALL return the existing error envelope
- **AND** it SHALL NOT return a successful zero count

#### Scenario: Observation cannot bypass the account proxy route
- **GIVEN** a paused account is bound to an unavailable proxy pool
- **WHEN** the dashboard requests its reset-credit count
- **THEN** the request MUST fail without direct upstream egress

