## ADDED Requirements

### Requirement: HTTP bridge account-cap pressure reclaims idle stream leases

When HTTP bridge session creation cannot select an account because every
otherwise eligible local candidate is blocked by `account_stream_cap`, the
service MUST attempt to recover capacity from an idle local HTTP bridge session
that still holds a stream lease before returning the local capacity failure.

The service MUST NOT reclaim a session whose account was not in the exact set
of otherwise eligible accounts rejected by the account-cap filter. It also
MUST NOT reclaim a session that has active or queued requests, an admission
waiter, a pre-submit handoff reservation, or no evidence that its first visible
turn has completed. API-key account assignment scopes and required
preferred-account continuity MUST be preserved when choosing a session to
reclaim. A soft preferred account SHOULD be reclaimed first when it has an
eligible idle session.

The registry lock MAY be used to inspect and detach the selected idle session,
but the session close and account-lease release MUST run through the existing
bounded cleanup path after the registry lock is released. Account selection
MUST be retried only after that bounded close has completed.

#### Scenario: Idle bridge releases the last local stream slot

- **GIVEN** an idle HTTP bridge session has completed a visible turn, has no
  pending or queued work, and still holds an account stream lease
- **AND** a new HTTP bridge session creation attempt receives
  `account_stream_cap`
- **WHEN** the idle session belongs to an account eligible for the new request
- **THEN** the service detaches and closes the idle session through the bounded
  cleanup path
- **AND** it retries account selection only after the idle session's stream
  lease has been released
- **AND** the new request can use the recovered stream slot

#### Scenario: Active bridge is not reclaimed

- **GIVEN** an HTTP bridge session has an active or queued request
- **WHEN** another bridge creation attempt receives `account_stream_cap`
- **THEN** the active session remains registered and retains its stream lease
- **AND** the service returns the existing bounded local capacity failure when
  no other eligible idle session can be reclaimed

#### Scenario: Required account continuity constrains reclamation

- **GIVEN** a bridge creation requires a preferred owner account
- **AND** only idle sessions on other accounts hold reclaimable stream leases
- **WHEN** account selection reports `account_stream_cap`
- **THEN** the service does not reclaim an unrelated account's session for that
  hard-continuity request
- **AND** the request fails closed with the existing account-capacity error

#### Scenario: Unrelated idle account is not reclaimed

- **GIVEN** account selection reports that a specific eligible account set is
  blocked only by `account_stream_cap`
- **AND** an idle session holds a stream lease on an account outside that set
- **WHEN** the service attempts local capacity recovery
- **THEN** the unrelated session remains registered and retains its lease
