## MODIFIED Requirements

### Requirement: Relative availability routing

The proxy account selector SHALL support a `relative_availability` routing strategy. The strategy SHALL evaluate only accounts that have passed the existing eligibility, health-tier, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates. Paused and deactivated accounts SHALL be hard-blocked. Re-authentication-required accounts SHALL be hard-blocked only when access credentials are known expired or carry proven `account_auth_invalidated` rejection; refresh-only warnings SHALL remain candidates subject to the other gates. For each candidate, the strategy SHALL compute a raw score from remaining secondary-window credits divided by seconds until the secondary-window reset, using bounded fallbacks for unknown or near-immediate reset times, and SHALL select from the highest weighted candidates according to the configured power and top-K cutoff.

#### Scenario: Soon-resetting usable credits are preferred
- **GIVEN** two healthy eligible accounts with equal remaining secondary credits
- **AND** one account's secondary window resets sooner
- **WHEN** account selection uses `relative_availability`
- **THEN** the sooner-resetting account receives the higher relative-availability score

#### Scenario: Relative availability preserves canonical gates
- **GIVEN** one account is paused, deactivated, rate-limited, quota-exceeded, cooling down, outside the requested model plan, or reauth-required with expired or proven-rejected access credentials
- **WHEN** account selection uses `relative_availability`
- **THEN** that account is not selected by the relative-availability strategy

#### Scenario: Relative availability retains refresh-only warning candidates
- **GIVEN** an otherwise eligible account is reauth-required only because its refresh credentials need repair
- **AND** its access credentials are not known expired and have no proven rejection
- **WHEN** account selection uses `relative_availability`
- **THEN** the account remains a routing candidate

### Requirement: Proven rejected reauthentication credentials stop routing

When an HTTP Responses access token is rejected with 401 and forced refresh fails permanently or the refreshed access token is again rejected with 401, the proxy MUST persist `reauth_required` with the existing `account_auth_invalidated` reason for that credential generation, unless the refresh handler has persisted a deactivating outcome. A deactivating refresh failure MUST retain `deactivated` and its original reason. Ordinary selection and live bridge reuse MUST reject either state even when JWT expiry is unknown or in the future. This requirement overrides warning-only routing only for proven access-authentication failure; a refresh-only credential-repair warning without an upstream access-token rejection MUST retain warning-only routing.

The status update MUST be conditioned on the rejected access-token and refresh-token ciphertexts and current status fields. A concurrent credential repair MUST NOT be overwritten or marked unavailable by the stale rejection. A later refresh-only failure MUST NOT weaken the persisted access-rejection reason. Reauthentication or reimport that repairs credentials and clears the reason MUST restore eligibility. Routing-unavailable evidence MUST survive a cache refresh and be observable by another replica through the routing availability snapshot.

A successful guarded token rotation that replaces the rejected access credentials after rejection commits MUST atomically clear that generation's `reauth_required` and `account_auth_invalidated` status. It MUST invalidate routing snapshots and reconcile the caller's account state. It MUST preserve unrelated paused, deactivated, quota, cooldown, and operator state. The same final eligibility MUST hold whether repair or rejection commits first.

#### Scenario: New messages avoid the rejected warning account

- **GIVEN** account A has unknown or future access-token expiry and account B is healthy
- **WHEN** A's upstream 401 is followed by permanent forced-refresh failure
- **THEN** a later independent message selects B without sending to A
- **AND** A remains visible as requiring reauthentication

#### Scenario: A refresh warning alone does not retire usable access

- **GIVEN** A requires reauthentication because its refresh token is invalid
- **WHEN** its stored access token has not been rejected and is not known expired
- **THEN** ordinary routing can still select A

#### Scenario: Concurrent access-only repair survives stale rejection

- **GIVEN** a request used A's old access token
- **WHEN** another actor replaces that access token before the rejection is persisted
- **THEN** the guarded rejection update does not modify the repaired row or publish an unavailable mark

#### Scenario: Repair clears the routing block

- **GIVEN** A was excluded for proven access-authentication failure
- **WHEN** repaired credentials are imported and routing snapshots refresh
- **THEN** A can be selected and reused again

#### Scenario: Repair invalidation wins over a completed rejection write

- **GIVEN** a guarded rejection write succeeds for account A
- **WHEN** credential repair is reflected in the local routing cache before the request publishes its unavailable mark
- **THEN** the stale rejection MUST NOT replace the repaired routing state
- **AND** a routing invalidation MUST still reconcile the final committed state

#### Scenario: A snapshot read before rejection cannot swallow its invalidation

- **GIVEN** a routing snapshot refresh observes A as active while its rejection write is pending
- **WHEN** the rejection write subsequently commits
- **THEN** the post-write invalidation MUST make the committed rejection visible within the cache-invalidation bus bound
#### Scenario: Deactivating forced refresh remains disabled after failover

- **GIVEN** an access rejection is followed by a forced refresh that deactivates the account
- **WHEN** the request recovers on another account or fails closed
- **THEN** the original account remains deactivated with the refresh failure reason

#### Scenario: Rotation repairs a rejection that committed first

- **GIVEN** an in-flight token exchange uses the unchanged refresh credential
- **AND** a peer commits access rejection for that credential generation first
- **WHEN** the exchange successfully rotates the access and refresh credentials
- **THEN** the matching rejection status is cleared atomically with token persistence
- **AND** local and peer routing snapshots converge to the repaired eligibility

### Requirement: Invalid refresh tokens require account re-authentication

The system MUST classify an upstream OAuth `invalid_refresh_token` refresh
failure as permanent, persist the affected account as re-authentication required
through the guarded refresh-account status path. Normal account selection MUST
exclude that account only when its access credentials are known expired or carry
proven access rejection; a refresh-only warning MUST retain ordinary eligibility.

#### Scenario: OAuth invalid-refresh-token response removes the account from routing

- **GIVEN** an active account attempts a token refresh
- **WHEN** upstream OAuth returns `invalid_refresh_token`
- **THEN** the refresh path persists the account status as `reauth_required`
- **AND** subsequent routing distinguishes refresh-only warnings from expired or
  proven-rejected access credentials

### Requirement: Re-authentication-required accounts remain request-routable

The system MUST distinguish request routability from refresh-token eligibility. `active` accounts MUST be request-routable. A `reauth_required` account MUST remain request-routable only while its stored access token is not known expired and has no proven `account_auth_invalidated` rejection; paused and deactivated accounts MUST remain excluded.

This status baseline is canonical for proxy selection, owner-bound affinity, warmup, automations, API-key account pools and scopes, probes, access-token-authenticated usage and reset-credit operations, and dashboard projections of routable capacity. Capability-specific references to active, eligible, or hard-unavailable accounts MUST apply this baseline unless a stricter credential-expiry, security, ownership, model, quota, cooldown, or operator-policy gate is explicitly required.

Selecting a routable `reauth_required` account MUST use its stored access token without proactive refresh-token exchange. Its sticky, bridge, file, response, and realtime ownership MUST remain bound while that token is unexpired and not proven rejected. Once a known access-token expiry is reached or access rejection is proven, new proxy selection and live bridge reuse MUST stop before upstream I/O. Movable soft affinity MAY fail over, while hard account-owned continuity MUST remain fail-closed rather than crossing accounts.

A permanent forced-refresh failure while serving a movable request MUST release the account's lease and exclude it from that request's remaining attempts. A refresh-only warning MUST NOT create a process-wide routing block before the stored access token's known expiry. Proven access rejection and deactivating refresh failures MUST block routing as specified above.

#### Scenario: Token-invalidated account remains in the pool

- **GIVEN** account A is `reauth_required` with a usable stored access token and no proven access rejection
- **WHEN** an ordinary proxy or supporting access-token operation selects an account
- **THEN** account A remains eligible after all other applicable gates
- **AND** its refresh token is not proactively exchanged

#### Scenario: Warning state preserves ownership

- **GIVEN** account A owns sticky or hard continuity
- **WHEN** account A becomes `reauth_required` with an unexpired stored access token and no proven access rejection
- **THEN** the ownership remains bound to account A
- **AND** the transition alone does not delete or rebind continuity

#### Scenario: Expired warning account is quiesced locally

- **GIVEN** account A is `reauth_required`
- **AND** its stored access token has reached its known expiry
- **WHEN** a new proxy request selects an account or considers bridge reuse
- **THEN** account A is rejected before upstream I/O
- **AND** hard account-owned continuity does not move to another account

#### Scenario: All expired warning accounts report reauthentication

- **GIVEN** every otherwise scoped account is `reauth_required` with a known-expired access token
- **AND** an additional-quota evidence gate would otherwise reject those accounts first
- **WHEN** account selection runs
- **THEN** selection fails with an explicit message that all accounts require reauthentication

#### Scenario: Current request excludes a rejected warning account

- **GIVEN** a movable request selected account A
- **AND** forced refresh fails permanently after upstream rejects A's access token
- **WHEN** the request retries selection
- **THEN** account A is excluded from that request's remaining attempts
- **AND** proven-rejected account A is excluded from later independent requests until credential repair

#### Scenario: Request-routable warning account cannot be newly assigned scoped routing

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is not offered as a new assignment until credential repair
- **AND** its existing ordinary routing eligibility is unchanged

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused or deactivated
- **WHEN** any routing strategy or account-scoped picker evaluates account A
- **THEN** account A is not selectable

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`
