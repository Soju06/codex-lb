# account-routing Delta

## MODIFIED Requirements

### Requirement: Stale in-memory account sessions must not stay routable

The service MUST remove accounts from routing when they are paused, deleted,
marked `reauth_required`, or otherwise made unavailable by a permanent
credential/session failure. This applies even when a long-lived in-memory HTTP
bridge session still holds an older `ACTIVE` account object. When the account
is successfully imported, re-authenticated, or reactivated, the service MUST
clear the in-memory unavailable marker. The routing-unavailable state MUST be
derived from persisted account status and MUST converge on every replica
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
