## MODIFIED Requirements

### Requirement: Reset credits are polled per account on a fixed cadence

The system SHALL poll upstream `GET /wham/rate-limit-reset-credits` for each eligible account on a configurable cadence that defaults to 60 seconds, using that account's stored OAuth bearer token and `chatgpt-account-id`. The scheduler SHALL always start with the application lifespan. Because snapshots are kept in process-local memory, every running replica SHALL refresh its own snapshot cache instead of relying on leader election, and the scheduler SHALL NOT be leader-gated while snapshots remain process-local. Each replica SHALL apply a randomized startup delay of up to one full interval and randomized per-tick jitter of +/-10% so replica ticks are desynchronized. The aggregate upstream fetch rate scales with the number of running replicas; `rate_limit_reset_credits_refresh_interval_seconds` is the operator control for total upstream load. The poll SHALL skip any account that is paused, requires reauthentication, deactivated, or lacks a usable `chatgpt-account-id`. When dashboard setting `auto_redeem_reset_credits_before_expiry` is enabled, the refresh loop SHALL evaluate refreshed snapshots and attempt to redeem the soonest-expiring available reset credit when it expires within five minutes by reusing the existing reset-credit redemption function, serialization, idempotency ledger, cache invalidation, and usage-refresh path. Before invoking the redemption function, automatic redemption SHALL re-read the target account in the redemption session and abort without consuming upstream when the account is missing, paused, requires reauthentication, deactivated, or no longer has a usable `chatgpt-account-id`. Automatic redemption SHALL constrain the redemption helper to the credit id and expiry that triggered the five-minute window, and SHALL abort without consuming upstream if the helper's fresh pre-consume fetch no longer reports that same credit with the same expiry as available. Automatic redemption SHALL use a stable automatic redeem request id for the account and UTC expiry date, and SHALL NOT issue another upstream consume when that automatic request is already durably pinned.

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
