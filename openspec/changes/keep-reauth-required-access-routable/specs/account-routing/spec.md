## MODIFIED Requirements

### Requirement: Re-authentication-required accounts are not selectable

When an account's refresh credential or session is invalidated but the upstream account is not known to be disabled, the system MUST mark the account `reauth_required`. The selector MUST continue treating `reauth_required` as request-routable with the stored access token while suppressing proactive refresh-token exchange. Paused and deactivated accounts MUST remain excluded from every routing strategy and hard-affinity fallback. Operator pickers that configure single-account or account-scoped routing MUST offer request-routable `reauth_required` accounts and MUST exclude paused or deactivated accounts.

A permanent refresh failure encountered while serving one request MUST exclude that account from the remainder of that request's movable retry loop. The exclusion MUST NOT become a process-wide routing block solely because the account remains `reauth_required`.

#### Scenario: Token invalidated account leaves the pool

- **GIVEN** account A is `reauth_required` and still has a usable stored access token
- **AND** account B is active
- **WHEN** a proxy request selects an account
- **THEN** account A remains an eligible candidate under the configured routing strategy
- **AND** selecting account A does not proactively exchange its known-bad refresh token

#### Scenario: Current request does not reselect a rejected warning account

- **GIVEN** a movable request selected account A
- **AND** account A's forced refresh fails permanently after upstream rejects its access token
- **WHEN** the request retries account selection
- **THEN** account A is excluded from that request's remaining attempts
- **AND** another eligible account may be selected
- **AND** account A remains `reauth_required` and may be considered by a later independent request

#### Scenario: Request-routable account can be selected for scoped routing

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is offered as a selectable account

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused or deactivated
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is not offered as a new selectable account

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`
