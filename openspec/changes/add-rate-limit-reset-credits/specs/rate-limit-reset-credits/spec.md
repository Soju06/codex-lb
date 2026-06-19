## ADDED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL always start with the application lifespan. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election. The poll SHALL skip any account that is paused, deactivated, or lacks a usable `chatgpt-account-id`.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts with default settings
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds

#### Scenario: Every replica refreshes its local cache
- **WHEN** the application is deployed with multiple running replicas
- **THEN** each replica refreshes its own in-memory reset-credit snapshots on the configured cadence
- **AND** dashboard reads served by any replica can observe populated reset-credit data after that replica's refresh tick

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

#### Scenario: In-flight refresh cannot restore an invalidated snapshot
- **GIVEN** a scheduler refresh starts fetching reset credits for an account
- **AND** another caller invokes `invalidate(account_id)` before that refresh stores its fetched response
- **WHEN** the refresh completes
- **THEN** the stale fetched response MUST NOT be written back into the cache

### Requirement: Operators can redeem the soonest-expiring available credit

The system SHALL expose a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems exactly one credit for the named account. The endpoint SHALL select, from the freshest cached snapshot, the credit whose `status` is `available` with the smallest `expires_at`, generate a `redeem_request_id` (UUID v4), and forward `{credit_id, redeem_request_id}` to upstream `POST /wham/rate-limit-reset-credits/consume` using the account's bearer token and `chatgpt-account-id`. A cached snapshot with `available_count <= 0` MUST be treated as having no redeemable credits, even if the cached `credits` list contains an item marked `available`. On a 200 response the endpoint SHALL invalidate the cached snapshot for that account and return `{code, windows_reset, redeemed_at}`. The endpoint SHALL require dashboard write access; read-only guests MUST be refused.

#### Scenario: Consume selects the soonest-expiring credit
- **GIVEN** an account has cached credits with expiries `2026-07-10Z` and `2026-06-20Z`, both `status: available`
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request forwarded to upstream carries the `credit_id` whose `expires_at` is `2026-06-20Z`

#### Scenario: Successful consume invalidates the cache
- **GIVEN** the operator invokes consume for an account with at least one available credit
- **WHEN** upstream returns `200` with `{code: "reset", windows_reset: 1, credit: {...}}`
- **THEN** the cached snapshot for that account is invalidated
- **AND** the response returned to the dashboard is `{code, windows_reset, redeemed_at}` derived from the upstream response

#### Scenario: Concurrent consume requests for one account are serialized
- **GIVEN** two operators invoke `POST /api/accounts/{id}/rate-limit-reset-credits/consume` at nearly the same time for the same account
- **WHEN** the first request is still redeeming a credit
- **THEN** the second request MUST wait for the first request to finish before re-reading that account's cached snapshot
- **AND** the same cached `credit_id` MUST NOT be sent to upstream twice by those concurrent requests

#### Scenario: Upstream consume failures surface as dashboard errors
- **GIVEN** an operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **WHEN** upstream returns `401`, `403`, or `409`
- **THEN** the dashboard endpoint returns the same client-facing status class instead of a generic `500`
- **AND** other upstream consume failures return a dashboard `503`

#### Scenario: Read-only guests cannot redeem
- **GIVEN** a dashboard session authenticated as a read-only guest
- **WHEN** the guest invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request is refused before any upstream call is made

#### Scenario: Consume with no available credit returns a client error
- **GIVEN** an account whose cached snapshot reports `available_count: 0` (or has no snapshot)
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error) without calling upstream

### Requirement: API-key self-service reset-credit reads and exact redemption reuse the cached snapshots

The system SHALL expose `GET /v1/reset-credit` and `POST /v1/reset-credit` as API-key-authenticated self-service routes backed by the same cached reset-credit snapshots used by the dashboard flow. `GET /v1/reset-credit` SHALL project the authenticated API key's eligible account pool into one array item per available credit, ordered by account email ascending, then account id ascending, then credit `expires_at` ascending with `null` expiries last, then credit id ascending. Each item SHALL include `account_id`, `email`, `redeem_id`, and `expiredAt`, where `redeem_id` and `expiredAt` come from that available credit. Accounts with no cached snapshot or no available credit SHALL be omitted from the `GET` response.

`POST /v1/reset-credit` SHALL accept JSON body `{account_id, redeem_id}`. The endpoint SHALL reject requests whose `account_id` is outside the authenticated API key's account pool. For in-pool accounts, the endpoint SHALL re-read that account's freshest cached snapshot, verify that `redeem_id` still identifies an `available` credit on the account, forward that exact `credit_id` upstream with a generated `redeem_request_id`, and invalidate the cached snapshot for the account on a 200 response. A cached snapshot with `available_count <= 0` MUST be treated as having no redeemable credits even if the cached `credits` list contains an item marked `available`.

#### Scenario: GET returns every available credit for eligible accounts
- **GIVEN** two in-pool accounts each have multiple cached available credits
- **WHEN** the client invokes `GET /v1/reset-credit`
- **THEN** the response contains one array item per available credit
- **AND** entries are grouped by account email and account id, with each account's credits ordered by soonest expiry first and `null` expiries last

#### Scenario: GET omits accounts without available cached credits
- **GIVEN** one in-pool account has `available_count: 0` and another has no cached snapshot
- **WHEN** the client invokes `GET /v1/reset-credit`
- **THEN** neither account appears in the response array

#### Scenario: POST rejects a redeem id that is not currently available
- **GIVEN** an in-pool account whose cached snapshot does not contain the supplied `redeem_id` as an `available` credit
- **WHEN** the client invokes `POST /v1/reset-credit`
- **THEN** the endpoint returns `409` without calling upstream

#### Scenario: POST forwards the exact requested redeem id
- **GIVEN** an in-pool account whose cached snapshot contains `redeem_id = "credit_exact"` as an available credit
- **WHEN** the client invokes `POST /v1/reset-credit` with `{account_id, redeem_id: "credit_exact"}`
- **THEN** the upstream consume request carries `credit_id = "credit_exact"`
- **AND** a successful response invalidates the cached snapshot for that account

### Requirement: Reset credit polling failure does not mutate account status

The reset-credits refresh scheduler SHALL NOT transition any account's persisted status (`active`, `rate_limited`, `quota_exceeded`, `paused`, `deactivated`) in response to upstream reset-credits responses. On upstream error (non-200, non-JSON, malformed 200 payload, network, or auth-like failure) the scheduler SHALL log the failure and either keep the prior cached snapshot or leave the cache unset; it SHALL NOT propagate the failure to account-status derivation.

#### Scenario: Upstream 401 on reset-credits does not deactivate the account
- **WHEN** the scheduler receives an HTTP `401` from `GET /wham/rate-limit-reset-credits` for an account
- **THEN** the account's persisted status is unchanged
- **AND** any prior cached snapshot for that account is retained

#### Scenario: Upstream 5xx retains the prior snapshot
- **GIVEN** an account has a cached snapshot from a prior successful tick
- **WHEN** the scheduler receives an HTTP `503` on the next reset-credits tick
- **THEN** the cached snapshot is retained
- **AND** the failure is logged

#### Scenario: Malformed 200 response is not cached as success
- **GIVEN** an account has a cached snapshot from a prior successful tick
- **WHEN** upstream returns HTTP `200` with a non-object body or a body missing required reset-credit fields
- **THEN** the response is treated as an upstream failure
- **AND** the cached snapshot is retained

### Requirement: Reset credit polling interval is configurable

The system SHALL expose setting `rate_limit_reset_credits_refresh_interval_seconds` (default `60`) to control the polling cadence. The system SHALL NOT expose a separate enable/disable toggle for reset-credit polling.

#### Scenario: Operator tunes the polling interval
- **GIVEN** `rate_limit_reset_credits_refresh_interval_seconds` is set to `120`
- **WHEN** the application starts and runs
- **THEN** each eligible account's credits are fetched from upstream at most once per 120 seconds
