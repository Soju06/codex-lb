## ADDED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The poll SHALL be leader-gated so that only one replica performs the polling in a multi-replica deployment. The poll SHALL skip any account that is paused, deactivated, or lacks a usable `chatgpt-account-id`.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the reset-credits refresh scheduler is enabled with default settings
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds

#### Scenario: Non-leader replica does not poll
- **WHEN** leader election reports the current replica is not the leader
- **THEN** the scheduler performs no upstream reset-credits fetches in that cycle

#### Scenario: Paused and deactivated accounts are skipped
- **WHEN** an account is persisted as `paused` or `deactivated`
- **THEN** the scheduler performs no upstream reset-credits fetch for that account
- **AND** the cached snapshot for that account (if any) is left untouched by the skip

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

### Requirement: Operators can redeem the soonest-expiring available credit

The system SHALL expose a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems exactly one credit for the named account. The endpoint SHALL select, from the freshest cached snapshot, the credit whose `status` is `available` with the smallest `expires_at`, generate a `redeem_request_id` (UUID v4), and forward `{credit_id, redeem_request_id}` to upstream `POST /wham/rate-limit-reset-credits/consume` using the account's bearer token and `chatgpt-account-id`. On a 200 response the endpoint SHALL invalidate the cached snapshot for that account and return `{code, windows_reset, redeemed_at}`. The endpoint SHALL require dashboard write access; read-only guests MUST be refused.

#### Scenario: Consume selects the soonest-expiring credit
- **GIVEN** an account has cached credits with expiries `2026-07-10Z` and `2026-06-20Z`, both `status: available`
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request forwarded to upstream carries the `credit_id` whose `expires_at` is `2026-06-20Z`

#### Scenario: Successful consume invalidates the cache
- **GIVEN** the operator invokes consume for an account with at least one available credit
- **WHEN** upstream returns `200` with `{code: "reset", windows_reset: 1, credit: {...}}`
- **THEN** the cached snapshot for that account is invalidated
- **AND** the response returned to the dashboard is `{code, windows_reset, redeemed_at}` derived from the upstream response

#### Scenario: Read-only guests cannot redeem
- **GIVEN** a dashboard session authenticated as a read-only guest
- **WHEN** the guest invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request is refused before any upstream call is made

#### Scenario: Consume with no available credit returns a client error
- **GIVEN** an account whose cached snapshot reports `available_count: 0` (or has no snapshot)
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error) without calling upstream

### Requirement: Reset credit polling failure does not mutate account status

The reset-credits refresh scheduler SHALL NOT transition any account's persisted status (`active`, `rate_limited`, `quota_exceeded`, `paused`, `deactivated`) in response to upstream reset-credits responses. On upstream error (non-200, non-JSON, network, or auth-like failure) the scheduler SHALL log the failure and either keep the prior cached snapshot or leave the cache unset; it SHALL NOT propagate the failure to account-status derivation.

#### Scenario: Upstream 401 on reset-credits does not deactivate the account
- **WHEN** the scheduler receives an HTTP `401` from `GET /wham/rate-limit-reset-credits` for an account
- **THEN** the account's persisted status is unchanged
- **AND** any prior cached snapshot for that account is retained

#### Scenario: Upstream 5xx retains the prior snapshot
- **GIVEN** an account has a cached snapshot from a prior successful tick
- **WHEN** the scheduler receives an HTTP `503` on the next reset-credits tick
- **THEN** the cached snapshot is retained
- **AND** the failure is logged

### Requirement: Reset credit polling is independently toggleable

The system SHALL expose settings `rate_limit_reset_credits_refresh_enabled` (default `true`) and `rate_limit_reset_credits_refresh_interval_seconds` (default `60`). When disabled, the scheduler SHALL perform no upstream reset-credits fetches and the in-memory store SHALL remain empty; the dashboard SHALL render zero reset affordances for every account.

#### Scenario: Disabled scheduler produces empty store
- **GIVEN** `rate_limit_reset_credits_refresh_enabled` is `false`
- **WHEN** the application starts and runs
- **THEN** no upstream reset-credits fetches are performed
- **AND** every account summary exposes `available_reset_credits: 0` and `reset_credit_nearest_expires_at: null`
