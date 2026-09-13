## REMOVED Requirements

### Requirement: Re-authentication-required accounts are not selectable

**Reason**: Refresh-token failure no longer proves that the stored access token is unusable.

**Migration**: Use the canonical request-routability requirement below.

## ADDED Requirements

### Requirement: Re-authentication-required accounts remain request-routable

The system MUST distinguish request routability from refresh-token eligibility. `active` accounts MUST be request-routable. A `reauth_required` account MUST remain request-routable only while its stored access token is not known to be expired; paused and deactivated accounts MUST remain excluded.

This status baseline is canonical for proxy selection, owner-bound affinity, warmup, automations, API-key account pools and scopes, probes, access-token-authenticated usage and reset-credit operations, and dashboard projections of routable capacity. Capability-specific references to active, eligible, or hard-unavailable accounts MUST apply this baseline unless a stricter credential-expiry, security, ownership, model, quota, cooldown, or operator-policy gate is explicitly required.

Selecting a routable `reauth_required` account MUST use its stored access token without proactive refresh-token exchange. Its sticky, bridge, file, response, and realtime ownership MUST remain bound while that token is unexpired. Once a known access-token expiry is reached, new proxy selection and live bridge reuse MUST stop before upstream I/O. Movable soft affinity MAY fail over, while hard account-owned continuity MUST remain fail-closed rather than crossing accounts.

A permanent forced-refresh failure while serving a movable request MUST release the account's lease and exclude it from that request's remaining attempts. The failure MUST NOT create a process-wide routing block before the stored access token's known expiry.

#### Scenario: Token-invalidated account remains in the pool

- **GIVEN** account A is `reauth_required` with a usable stored access token
- **WHEN** an ordinary proxy or supporting access-token operation selects an account
- **THEN** account A remains eligible after all other applicable gates
- **AND** its refresh token is not proactively exchanged

#### Scenario: Warning state preserves ownership

- **GIVEN** account A owns sticky or hard continuity
- **WHEN** account A becomes `reauth_required` with an unexpired stored access token
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
- **AND** account A may still be considered by a later independent request

#### Scenario: Request-routable account can be selected for scoped routing

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is offered as selectable

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused or deactivated
- **WHEN** any routing strategy or account-scoped picker evaluates account A
- **THEN** account A is not selectable

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`
