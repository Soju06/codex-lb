## MODIFIED Requirements

### Requirement: Re-authentication-required accounts remain request-routable

The system MUST distinguish request routability from refresh-token eligibility.
`active` accounts MUST be request-routable. A `reauth_required` account MUST
remain request-routable only while its stored access token is not known to be
expired, unless its deactivation reason explicitly proves that the access token
was invalidated or revoked; paused and deactivated accounts MUST remain
excluded.

This status baseline is canonical for proxy selection, owner-bound affinity,
warmup, automations, API-key account pools and scopes, probes,
access-token-authenticated usage and reset-credit operations, and dashboard
projections of routable capacity. Capability-specific references to active,
eligible, or hard-unavailable accounts MUST apply this baseline unless a
stricter credential-expiry, security, ownership, model, quota, cooldown, or
operator-policy gate is explicitly required.

Selecting a routable `reauth_required` account MUST use its stored access token
without proactive refresh-token exchange. Its sticky, bridge, file, response,
and realtime ownership MUST remain bound while that token is unexpired. Once a
known access-token expiry is reached, or an explicit invalidation/revocation
reason is recorded, new proxy selection and live bridge reuse MUST stop before
upstream I/O. Movable soft affinity MAY fail over, while hard account-owned
continuity MUST remain fail-closed rather than crossing accounts.

A permanent forced-refresh failure while serving a movable request MUST
release the account's lease and exclude it from that request's remaining
attempts. The failure MUST NOT create a process-wide routing block before the
stored access token's known expiry unless the failure reason explicitly proves
that the stored access token was invalidated or revoked. Such an explicit
reason MUST be propagated to routing caches so every replica excludes the
account, even while the persisted row is still `ACTIVE` or
`REAUTH_REQUIRED` during guarded settlement.

#### Scenario: Token-invalidated account remains in the pool

- **GIVEN** account A is `reauth_required` only because its refresh token is
  invalidated and its stored access token remains usable
- **WHEN** an ordinary proxy or supporting access-token operation selects an
  account
- **THEN** account A remains eligible after all other applicable gates
- **AND** its refresh token is not proactively exchanged

#### Scenario: Explicitly revoked access token stays out of new routing

- **GIVEN** account A is `reauth_required` because its access token was
  explicitly revoked or invalidated
- **WHEN** the proxy selects an account for a later request
- **THEN** account A is not eligible even when the stored token has no
  parseable expiration
- **AND** the routing-unavailable reason is visible to peer replicas

#### Scenario: Warning state preserves ownership

- **GIVEN** account A owns sticky or hard continuity
- **WHEN** account A becomes `reauth_required` with an unexpired stored access
  token and no explicit access-token invalidation reason
- **THEN** the ownership remains bound to account A
- **AND** the transition alone does not delete or rebind continuity

#### Scenario: Expired warning account is quiesced locally

- **GIVEN** account A is `reauth_required`
- **AND** its stored access token has reached its known expiry
- **WHEN** a new proxy request selects an account or considers bridge reuse
- **THEN** account A is rejected before upstream I/O
- **AND** hard account-owned continuity does not move to another account

#### Scenario: Explicitly revoked warning account is quiesced locally

- **GIVEN** account A is `reauth_required` with an explicit access-token
  invalidation or revocation reason
- **WHEN** a new proxy request selects an account or considers bridge reuse
- **THEN** account A is rejected before upstream I/O
- **AND** hard account-owned continuity does not move to another account

#### Scenario: All expired warning accounts report reauthentication

- **GIVEN** every otherwise scoped account is `reauth_required` with a
  known-expired access token
- **AND** an additional-quota evidence gate would otherwise reject those
  accounts first
- **WHEN** account selection runs
- **THEN** selection fails with an explicit message that all accounts require
  reauthentication

#### Scenario: Current request excludes a rejected warning account

- **GIVEN** a movable request selected account A
- **AND** forced refresh fails permanently after upstream rejects A's access
  token
- **WHEN** the request retries selection
- **THEN** account A is excluded from that request's remaining attempts
- **AND** account A may still be considered by a later independent request
  unless the failure reason explicitly invalidates or revokes its access token

#### Scenario: Request-routable account can be selected for scoped routing

- **GIVEN** account A is `reauth_required` without a known-expired access token
  and without an explicit access-token invalidation reason
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is offered as selectable

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused or deactivated, or is explicitly blocked by an
  access-token invalidation or revocation reason
- **WHEN** any routing strategy or account-scoped picker evaluates account A
- **THEN** account A is not selectable

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`
