## ADDED Requirements

### Requirement: Completed request-scoped bridge forks retire when quiescent

The HTTP Responses bridge MUST retire an ordinary
`internal_unanchored_parallel` request-scoped lane after a successful terminal
response when the lane has no pending or queued requests, admission waiters,
or pre-submit handoff reservation. Retirement MUST close the upstream socket
and release the account stream lease through the normal full-session cleanup
path. The service MUST NOT release a stream lease while leaving its upstream
socket reusable.

Retirement MUST require a confirmed durable alias for the completed response
and preserve that alias so a later explicit continuation remains bound to the
original owner account. If durable alias persistence is unavailable, the live
fork MUST remain available as the local continuity fallback. Retirement MUST
NOT apply to canonical session-header, turn-state, prompt-cache, or
account-neutral recovery lanes.

#### Scenario: Completed ordinary fork returns its live resources

- **GIVEN** an ordinary request-scoped unanchored fork has one active request
- **WHEN** the request emits `response.completed` and the lane becomes quiescent
- **THEN** the service closes the fork's upstream socket
- **AND** releases its account stream lease
- **AND** removes the live fork from local reuse

#### Scenario: Concurrent work prevents premature retirement

- **GIVEN** an ordinary unanchored fork has another pending or admitted request
- **WHEN** one request completes
- **THEN** the service does not retire the lane
- **AND** it keeps the account stream lease while the upstream socket remains reusable

#### Scenario: Durable continuation survives live fork retirement

- **GIVEN** a completed fork committed a previous-response or turn-state alias
- **WHEN** the live fork is retired and a later request resolves that alias
- **THEN** durable recovery keeps the continuation on the original account
- **AND** the service does not treat the continuation as unanchored work

#### Scenario: Durable persistence failure keeps the local fallback

- **GIVEN** an ordinary fork completed and published a local previous-response alias
- **WHEN** persisting that alias to durable storage fails
- **THEN** the service does not retire the live fork under the completed-fork rule
- **AND** the local alias remains available for explicit continuation

#### Scenario: Late admission cannot dispatch onto a retiring fork

- **GIVEN** a continuation started admission while an ordinary fork was completing
- **WHEN** durable completion marks the fork to retire before upstream dispatch
- **THEN** admission rechecks retirement under lifecycle ownership
- **AND** the continuation does not send work onto the closing upstream socket

#### Scenario: Account-neutral recovery lane keeps its lifecycle

- **GIVEN** an `internal_unanchored_parallel` lane uses the server
  `account-neutral-replay:v1:` key family
- **WHEN** its request completes
- **THEN** completed-fork retirement does not close it under the ordinary fork rule
