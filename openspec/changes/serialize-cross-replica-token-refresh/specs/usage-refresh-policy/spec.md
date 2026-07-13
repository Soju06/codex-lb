# usage-refresh-policy (delta)

## ADDED Requirements

### Requirement: Cross-replica token refresh serialization

Before any upstream OAuth token exchange for an account, the system MUST acquire that account's row in `account_refresh_claims` via a conditional upsert that succeeds only when no unexpired claim by another claimant exists; the upsert MUST be atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock). After acquiring, the system MUST re-read the account's refresh-token material fresh from the database (bypassing session identity caches) and MUST skip the upstream exchange when the material has rotated since the refresh was requested, adopting the stored tokens instead. Claims MUST carry an expiry covering all work performed under the claim (TTL at least the refresh-admission wait timeout plus twice the refresh HTTP timeout, because the claim is held across the admission wait and the OAuth exchange) so a crashed claimant cannot block refresh indefinitely while a healthy claimant cannot lose its claim mid-work, MUST be released after the refreshed tokens are persisted, and MUST NOT be held as an open database transaction or lock across upstream network I/O. The claimant identity MUST remain unique per process even when the configured instance id exceeds the stored column width (truncate the instance-id portion, never the per-process suffix).

After a successful upstream exchange, the system MUST persist the newly issued tokens with a compare-and-set conditioned on the refresh-token ciphertext the exchange was requested with. When that compare-and-set misses, the system MUST NOT assume any ciphertext change is a newer rotation: it MUST compare the freshly observed refresh-token material against the material this attempt exchanged (by decrypted-plaintext fingerprint, because token ciphertext is non-deterministic and a concurrent re-authentication or import can re-encrypt the same plaintext to different bytes). Only when the stored material is a genuinely different refresh token MUST the system adopt the stored row without persisting its own result; when the stored material is the same plaintext merely re-encrypted, the system MUST retry the compare-and-set against the freshly observed ciphertext (bounded) so its own single-use rotation is persisted rather than discarding it and leaving the account holding the token it already consumed upstream.

#### Scenario: Two replicas force-refresh the same account concurrently

- **GIVEN** two replicas hold the same refresh-token material for one account
- **WHEN** both trigger a forced token refresh concurrently (for example after a shared upstream 401)
- **THEN** exactly one upstream token exchange occurs
- **AND** the account remains `active`
- **AND** both replicas end up with the rotated token material
- **AND** the account's sticky sessions and bridge sessions are untouched

#### Scenario: Claimant crashes mid-refresh

- **GIVEN** a replica acquired the refresh claim for an account and crashed before releasing it
- **WHEN** another replica attempts to refresh the account after the claim TTL has elapsed
- **THEN** the claim acquisition succeeds and the refresh proceeds

#### Scenario: Winner adopts a rotation that landed before its claim

- **GIVEN** a replica acquires the refresh claim for an account
- **AND** the freshly re-read refresh-token material differs from the material the refresh was requested with
- **WHEN** the replica proceeds
- **THEN** it returns the stored tokens without any upstream token exchange

#### Scenario: Persistence compare-and-set misses on a re-encryption of the same token

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** a concurrent re-authentication/import re-encrypted the SAME refresh-token plaintext to different ciphertext, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint unchanged from the material it exchanged
- **THEN** it retries the compare-and-set against the freshly observed ciphertext and persists its own newly issued tokens
- **AND** it does not adopt the re-encrypted, already-consumed token

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation

- **GIVEN** a replica completed a successful upstream token exchange
- **AND** a peer committed a genuinely different refresh token, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint changed
- **THEN** it adopts the peer's stored tokens without persisting its own result

### Requirement: Refresh claim losers wait bounded and never degrade account status

A process that fails to acquire the refresh claim MUST wait by polling within a bounded deadline (configurable cap, additionally bounded by the caller's refresh timeout budget). When it observes rotated refresh-token material it MUST return the stored tokens without an upstream call. When the deadline elapses it MUST fail with a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown, and it MUST NOT write `reauth_required` or `deactivated`, so token-refresh recovery fails over to another account instead of blocking.

When a proxy stream turn encounters this transient claim failure, the streaming retry loop MUST exclude the affected account and fail over to a different account rather than reselecting the claimed account until attempts are exhausted. This failover MUST apply to both the proactive freshness check on the first stream attempt (before any upstream 401) and the forced refresh on the post-401 recovery attempt. Before failing over, the loop MUST release the stream lease it already acquired for the skipped account so that account does not continue to consume one of its stream-concurrency slots for a stream that will never open.

#### Scenario: Claim held by another replica past the wait cap

- **GIVEN** an unexpired refresh claim held by another replica
- **WHEN** a refresh waits past the configured wait cap without observing rotated token material
- **THEN** the refresh fails with a transient, non-permanent error
- **AND** the account status is unchanged
- **AND** sticky and bridge sessions are untouched
- **AND** the failure is not cached as a permanent refresh failure

#### Scenario: Winner finishes within the wait cap

- **GIVEN** an unexpired refresh claim held by another replica that completes its token exchange
- **WHEN** the waiting replica observes the rotated refresh-token material within the wait cap
- **THEN** it returns the rotated tokens with zero upstream token exchanges

#### Scenario: Proactive pre-stream claim timeout fails over instead of looping

- **GIVEN** a proxy stream turn whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the first-attempt freshness check raises the transient claim error before any upstream 401
- **THEN** the streaming retry loop excludes that account and fails over to a healthy account
- **AND** the excluded account's already-acquired stream lease is released before failover
- **AND** the request does not exhaust attempts as `no_accounts` while a healthy alternate exists

## MODIFIED Requirements

### Requirement: token_expired at the refresh boundary deactivates the account

The system MUST treat OAuth refresh credential-token or session errors as
permanent refresh-token/session failures. Codes include `token_expired`,
`app_session_terminated`, `invalid_grant`, `refresh_token_expired`,
`refresh_token_reused`, and `refresh_token_invalidated`. The affected account
MUST be marked `reauth_required` and removed from the routing pool until it is
re-authenticated.

Before persisting a permanent refresh failure, the system MUST re-read the
account's token material from the database with a real SELECT that bypasses
session identity caches, MUST NOT downgrade the account when the refresh token
rotated after the failed attempt began (returning the rotated tokens instead),
and MUST apply the status downgrade with a compare-and-set conditioned on the
freshly observed account state including the refresh-token ciphertext, so a
concurrent re-authentication or rotation — even one that leaves
status/reason/reset untouched — is never overwritten.

#### Scenario: Refresh-time `app_session_terminated` is classified as permanent

- **WHEN** `classify_refresh_error("app_session_terminated")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `app_session_terminated` requires re-authentication

- **WHEN** `AuthManager.refresh_account` receives a
  `RefreshError("app_session_terminated", ..., is_permanent=True)` from
  `refresh_access_token`
- **THEN** the account is transitioned to `REAUTH_REQUIRED`
- **AND** the reason references the re-login requirement so the dashboard can
  surface it
- **AND** the account is no longer selected by the load balancer until it is
  re-authenticated

#### Scenario: Concurrent rotation loser receives refresh_token_reused

- **GIVEN** another replica rotated the account's refresh token and committed
  while this replica's exchange with the old token was in flight
- **WHEN** this replica's exchange fails with `refresh_token_reused`
- **THEN** no `reauth_required` write occurs
- **AND** this replica returns the rotated tokens from the database

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. When leader election is disabled, the guardian MUST detect multi-replica operation dynamically from live bridge ring membership (members with a heartbeat within the staleness threshold) in addition to the static instance ring, MUST skip the refresh pass when more than one live replica is detected, and MUST log a warning identifying the leader-election setting.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Dynamically registered replicas without leader election

- **GIVEN** two replicas registered in `bridge_ring_members` with live heartbeats
- **AND** the static instance ring is empty
- **AND** leader election is disabled
- **WHEN** an Auth Guardian tick runs on either replica
- **THEN** the guardian performs no refresh work
- **AND** logs a warning identifying the leader-election setting
