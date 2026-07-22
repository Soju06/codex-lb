## ADDED Requirements

### Requirement: HTTP bridge security retries fail closed after an anchor or output

For HTTP bridge requests, the service MUST retry security-work authorization on
another account only before `response.created` and before any upstream model
output. A buffered reasoning prelude counts as upstream model output even while
it is withheld from downstream pending the security decision. A permitted
file-free retry MUST select the replacement with cleared request and session
affinity, but MUST validate any raw legacy owner before changing the live
session or its durable owner generation. On success it MUST make exactly one
durable replacement claim before swapping the session, then clear or replace
the session affinity and local turn-state aliases. A legacy-owner conflict MUST
leave the original session open and unchanged. File-pinned requests MUST NOT
migrate. A pre-created reconnect for a hard session-header bridge MUST remain
bound to its established account, and a replacement connection MUST NOT inherit
a stale turn-state header when it has no replacement turn state. If a permitted
security retry swaps durable ownership but cannot submit `response.create` on
the replacement socket, the replacement bridge MUST be retired rather than
left reusable with partially rebound aliases. Once a reasoning prelude is
buffered, all following reasoning-prelude events MUST remain buffered until the
terminal security decision.
For a direct WebSocket security retry, the service MUST reacquire the per-session
create gate and shared/account create admission before queuing or sending the
replay on the authorized replacement socket.

#### Scenario: Created HTTP bridge response is not replayed

- **WHEN** an HTTP bridge request has emitted `response.created` before a
  security-work authorization denial
- **THEN** the service does not reconnect or resend the request on another account
- **AND** it forwards the original terminal error

#### Scenario: Deferred reasoning blocks replay

- **WHEN** an HTTP bridge request buffers a reasoning prelude before a
  security-work authorization denial
- **THEN** that prelude blocks account-switch replay and is not emitted before
  the terminal security decision

#### Scenario: Hard session reconnect preserves one owner

- **GIVEN** a pre-created HTTP bridge request uses a hard session-header key
- **WHEN** its upstream socket closes before visible output
- **THEN** the reconnect requires the established account
- **AND** the session turn-state and owner affinity are not cleared for migration

#### Scenario: Failed replacement resend retires the bridge

- **GIVEN** a permitted security retry has swapped the bridge to an authorized account
- **WHEN** submitting `response.create` on that replacement socket fails
- **THEN** the replacement bridge is marked for retirement
- **AND** it is not left reusable with partially rebound continuity aliases

#### Scenario: Direct WebSocket security replay reacquires admission

- **GIVEN** a direct WebSocket security denial releases the first attempt's create admission
- **WHEN** the request is replayed on an authorized replacement account
- **THEN** the service reacquires create admission before queuing the replay
- **AND** the replay remains subject to the normal startup timeout and serialization gate

#### Scenario: Legacy owner conflict fails before replacement mutation

- **GIVEN** a session-header security retry selects an authorized replacement account
- **AND** the raw legacy affinity row belongs to a different account
- **WHEN** the service validates the replacement
- **THEN** it does not claim the durable session for the replacement
- **AND** it leaves the original account, upstream, owner generation, aliases, and open session unchanged
