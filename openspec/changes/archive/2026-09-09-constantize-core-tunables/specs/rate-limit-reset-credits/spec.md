## MODIFIED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a fixed 60-second cadence, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL start with the application lifespan when reset-credit polling is enabled. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas (one fetch per eligible account per 60 seconds per replica); the cadence is a fixed application constant and MUST NOT be operator-configurable. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`.

When dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the refresh loop SHALL evaluate refreshed snapshots and attempt to redeem the soonest-expiring available reset credit when it expires within five minutes by reusing the existing reset-credit redemption function, serialization, idempotency ledger, cache invalidation, and usage-refresh path. Before invoking the redemption function, automatic redemption SHALL re-read the target account in the redemption session and abort without consuming upstream when the account is missing, paused, requires reauthentication, deactivated, or no longer has a usable `chatgpt-account-id`. Automatic redemption SHALL constrain the redemption helper to the credit id and expiry that triggered the five-minute window, and SHALL abort without consuming upstream if the helper's fresh pre-consume fetch no longer reports that same credit with the same expiry as available. Automatic redemption SHALL use a stable automatic redeem request id for the account and UTC expiry date, and SHALL NOT issue another upstream consume when that automatic request is already durably pinned.

#### Scenario: Default cadence polls every 60 seconds
- **WHEN** the application starts
- **THEN** each eligible account's credits are fetched from upstream at most once per 60 seconds plus the jitter bound
- **AND** a `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS` value in the environment is ignored with the removed-setting warning

#### Scenario: Every replica refreshes its local cache
- **WHEN** the application is deployed with multiple running replicas
- **THEN** each replica refreshes its own in-memory reset-credit snapshots on the fixed cadence
- **AND** dashboard reads served by any replica can observe populated reset-credit data after that replica's refresh tick

#### Scenario: Two replicas do not fetch in lockstep
- **GIVEN** two replicas start
- **WHEN** their refresh loops run
- **THEN** their startup delays are independent uniform draws over the full interval and each tick interval carries independent +/-10% jitter, so the replicas' tick times are not synchronized

#### Scenario: Ineligible accounts are skipped
- **WHEN** an account is persisted as `paused`, `reauth_required`, or `deactivated`
- **THEN** the scheduler performs no upstream reset-credits fetch for that account
- **AND** the cached snapshot for that account (if any) is left untouched by the skip

#### Scenario: Automatic redemption is disabled by default
- **WHEN** the dashboard settings row is created for the first time
- **THEN** `auto_redeem_reset_credits_before_expiry` is `false`
- **AND** the reset-credit refresh scheduler only refreshes snapshots and does not redeem credits automatically

#### Scenario: Automatic redemption reuses the existing redeem path
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot includes an available credit whose expiry is within the automatic redemption window
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system redeems the soonest-expiring available credit through the same redemption function used by the dashboard consume endpoint
- **AND** the redemption uses the existing per-account serializer, durable idempotency ledger, cache invalidation, and usage refresh behavior
- **AND** duplicate automatic attempts for an already pinned automatic request do not issue another upstream consume

#### Scenario: Automatic redemption ignores non-expiring snapshots
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot has no available credit with `expires_at`
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system does not attempt automatic redemption for that snapshot

#### Scenario: Automatic redemption waits until the five-minute expiry window
- **GIVEN** `auto_redeem_reset_credits_before_expiry` is enabled
- **AND** a refreshed eligible account snapshot's soonest available credit expires more than five minutes in the future
- **WHEN** the reset-credit refresh loop processes that account
- **THEN** the system refreshes the snapshot but does not attempt automatic redemption

## ADDED Requirements

### Requirement: Reset credit polling can be disabled

The system SHALL expose setting `rate_limit_reset_credits_refresh_enabled` (default `true`) to enable or disable background reset-credit polling. Because the refresh loop is the sole driver of automatic reset-credit redemption, disabling background polling SHALL also disable automatic redemption; when polling is disabled while the persisted dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the system SHALL log a configuration-conflict warning at startup naming both settings. While polling is disabled, the dashboard settings update SHALL reject a request that newly enables `auto_redeem_reset_credits_before_expiry` with a bad-request error naming the polling toggle; an already-persisted opt-in SHALL remain readable and re-savable so unrelated settings edits are not blocked.

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

## REMOVED Requirements

### Requirement: Reset credit polling interval is configurable

**Reason**: The polling interval was never tuned by any deployment and becomes a fixed 60-second constant (`_REFRESH_INTERVAL_SECONDS` in `app/core/usage/reset_credits_refresh_scheduler.py`). The enable/disable toggle it also described is unchanged and moves verbatim to "Reset credit polling can be disabled".

**Migration**: Remove `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS` from the environment; startup warns once while it is still set.
