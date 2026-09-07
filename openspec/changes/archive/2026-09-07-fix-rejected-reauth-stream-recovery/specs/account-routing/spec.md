## ADDED Requirements

### Requirement: Proven rejected reauthentication credentials stop routing

When an HTTP Responses access token is rejected with 401 and forced refresh fails permanently or the refreshed access token is again rejected with 401, the proxy MUST persist `reauth_required` with the existing `account_auth_invalidated` reason for that credential generation. Ordinary selection and live bridge reuse MUST reject that state even when JWT expiry is unknown or in the future. This requirement overrides warning-only routing only for this proven access-authentication failure; a permanent refresh failure without an upstream access-token rejection MUST retain the existing warning-only behavior.

The status update MUST be conditioned on the rejected access-token and refresh-token ciphertexts and current status fields. A concurrent credential repair MUST NOT be overwritten or marked unavailable by the stale rejection. A later refresh-only failure MUST NOT weaken the persisted access-rejection reason. Reauthentication or reimport that repairs credentials and clears the reason MUST restore eligibility. Routing-unavailable evidence MUST survive a cache refresh and be observable by another replica through the routing availability snapshot.

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

## RENAMED Requirements

- FROM: `### Requirement: Re-authentication-required accounts are not selectable`
- TO: `### Requirement: Re-authentication-required routing distinguishes usable access credentials`

## MODIFIED Requirements

### Requirement: Stale in-memory account sessions must not stay routable

The service MUST remove accounts from routing when they are paused, deleted,
deactivated, or marked `reauth_required` with known expired access credentials
or proven `account_auth_invalidated` access rejection. A refresh-only
`reauth_required` warning MUST NOT by itself remove an account whose access
token is not known expired from routing. This applies even when a long-lived in-memory HTTP
bridge session still holds an older `ACTIVE` account object. When the account
is successfully imported, re-authenticated, or reactivated, the service MUST
clear the in-memory unavailable marker. The routing-unavailable state MUST be
derived from persisted account status and authentication-failure reason, with
known access-token expiry checked during selection and reuse, and MUST converge on every replica
within the cache-invalidation bus bound (marks and clears both propagate);
bridge-session reuse checks MUST NOT add per-request database reads; sessions
pinned to a deleted account MUST NOT be reused on any replica. A local
unavailable mark set while a snapshot refresh is in flight MUST survive that
refresh: a refresh MUST NOT clear marks it could not have observed as committed
status when its database read started.

#### Scenario: Stale bridge session is not reused after account becomes unavailable

- **GIVEN** an HTTP bridge session was created while account A was active
- **AND** account A is later marked unavailable for routing
- **WHEN** a subsequent bridge request looks for a reusable session
- **THEN** the stale session for account A is not reused

#### Scenario: Re-authentication clears routing-unavailable state

- **GIVEN** account A was marked unavailable after a credential/session failure,
  including on a replica other than the one handling the re-authentication
- **WHEN** account A is re-authenticated successfully
- **THEN** account A is eligible for routing again subject to normal account
  selection gates on every replica after the invalidation bus converges,
  without requiring a process restart

#### Scenario: Pause on one replica stops bridge-session reuse on peers

- **GIVEN** replica B holds a warm HTTP bridge session pinned to account A whose in-memory snapshot reads `ACTIVE`
- **WHEN** account A is paused via a request served by replica A
- **THEN** after the invalidation bus converges, replica B refuses to reuse the warm bridge session for account A

#### Scenario: Local mark set during an in-flight snapshot refresh is preserved

- **GIVEN** a routing snapshot refresh is in flight and its database read observed
  account A as `ACTIVE` before a permanent failure was committed
- **WHEN** account A is marked routing-unavailable locally before that refresh
  finishes
- **THEN** the completed refresh MUST NOT drop the local mark based on its stale
  snapshot, and account A remains routing-unavailable on that replica until a
  later refresh observes a committed routable status

#### Scenario: Deletion on one replica stops bridge-session reuse on peers

- **GIVEN** replica B holds a warm HTTP bridge session pinned to account A whose in-memory snapshot reads `ACTIVE`
- **WHEN** account A is deleted via a request served by replica A
- **THEN** after the invalidation bus converges, replica B treats account A as routing-unavailable even though its in-memory account object still reads `ACTIVE`

### Requirement: Re-authentication-required routing distinguishes usable access credentials

When account refresh credentials need repair but the upstream account is not known to be disabled, the system MUST mark the account `reauth_required`. The selector MUST retain refresh-only warning accounts whose access tokens are not known expired as candidates, subject to normal routing constraints. The selector MUST exclude `reauth_required` accounts with known expired access credentials or proven `account_auth_invalidated` access rejection from every routing strategy and hard-affinity fallback until credential repair. Hard account-owned continuity MUST remain fail-closed when its owner is excluded.

Operator pickers that configure new single-account or account-scoped assignments MUST continue to omit paused, `reauth_required`, and deactivated accounts. This operator-assignment restriction MUST NOT remove refresh-only warning accounts from ordinary routing or existing ownership. Reauthentication-required accounts MUST NOT be paused into a resumable state.

#### Scenario: Token invalidated account leaves the pool

- **GIVEN** account A is `reauth_required`
- **AND** its access credentials are known expired or carry the proven `account_auth_invalidated` reason
- **AND** account B is active
- **WHEN** a proxy request selects an account
- **THEN** account B is selected
- **AND** account A is not considered an eligible candidate

#### Scenario: Refresh-only warning remains an ordinary routing candidate

- **GIVEN** account A is `reauth_required` only because its refresh token needs repair
- **AND** its access token is not known expired and has no proven access-rejection reason
- **WHEN** a proxy request selects an account or reuses existing ownership
- **THEN** account A remains eligible subject to normal routing constraints

#### Scenario: Account requiring operator repair cannot be newly selected for scoped routing

- **GIVEN** account A is paused, reauth-required, or deactivated
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is not offered as a new selectable account

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`
