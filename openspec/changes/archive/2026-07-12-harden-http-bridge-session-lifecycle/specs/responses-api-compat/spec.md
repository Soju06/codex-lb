## ADDED Requirements

### Requirement: HTTP bridge stale-session cleanup is bounded

The HTTP responses bridge MUST NOT hold the global bridge session registry lock
while awaiting operations that can block on a stale session's upstream websocket,
per-session pending lock, durable session repository, account lease release, or
other external cleanup work.

When stale bridge sessions are discovered during `/v1/responses`,
`/backend-api/codex/responses`, `/v1/responses/compact`, or
`/backend-api/codex/responses/compact` startup, the registry lock MAY be used to
remove closed or idle sessions from in-memory indexes, but potentially blocking
session close/fail-pending work MUST run after the lock is released or under a
bounded cleanup path. A wedged stale session MUST NOT prevent unrelated soft
HTTP Responses work from creating or reusing another bridge session.

Idle pruning MUST make pending-request decisions only while holding the
session's pending-request lock. If that lock cannot be acquired immediately,
the service MUST skip pruning that session instead of inferring that it is idle
from unlocked pending-request state.

If cleanup cannot complete within the bounded cleanup path, the service MUST log
a low-cardinality local bridge cleanup warning and continue protecting registry
progress. Requests that cannot safely proceed because a hard-continuity session
is unavailable MUST fail closed with an explicit local overload or continuity
error rather than silently hanging.

When a replacement bridge session claims the same durable key after stale local
session detachment, the durable owner generation MUST advance so that a late
cleanup from the stale local session cannot release or close the replacement
session's durable ownership. This MUST also apply when the detached local
session is retiring but still has visible in-flight requests and will release
its durable ownership later after draining. After a detached retiring session
finishes draining its visible requests, it MUST release its durable ownership
and account lease instead of only closing the upstream websocket.
If that retirement is initiated by the upstream-reader task after processing
the terminal upstream event, session close MUST NOT cancel or await the current
upstream-reader task itself.

When bridge capacity eviction removes an idle local session to admit a
replacement session, the evicted session's close MUST be awaited through a
bounded path before the replacement selects an account, so the evicted
session's account lease cannot cause a spurious no-account or local-capacity
failure.

If a request is cancelled while awaiting that pre-creation eviction close after
registering replacement session creation as in-flight, the service MUST fail or
remove the in-flight creation marker before propagating cancellation. Later
requests MUST NOT wait on an orphaned creation future that can never complete.

#### Scenario: wedged stale pending lock does not block fresh soft request

- **GIVEN** the HTTP responses bridge has an idle or stale local session whose
  pending-request lock does not complete promptly
- **WHEN** a new soft-affinity `/v1/responses` request starts bridge session
  selection
- **THEN** the global bridge registry lock is not held indefinitely by stale
  cleanup
- **AND** the stale session is not pruned based on unlocked pending-request
  state
- **AND** the new request either creates/reuses an eligible bridge session or
  returns an explicit bounded local error
- **AND** it does not hang before account selection or bridge create/reuse
  logging

#### Scenario: stale close runs outside registry lock

- **GIVEN** bridge startup identifies an idle stale session that must be closed
- **WHEN** closing that session awaits upstream-reader cancellation, websocket
  close, durable release, or account lease release
- **THEN** the global bridge registry lock is already released
- **AND** unrelated bridge startup requests can continue to inspect or mutate
  the registry

#### Scenario: stale durable release cannot fence out replacement owner

- **GIVEN** a stale or retiring bridge session for a durable key is replaced by
  a new local session after local detachment
- **WHEN** the stale session's bounded background close releases durable
  ownership after the replacement has claimed the same durable key
- **THEN** the stale release does not clear the replacement owner's durable
  lease
- **AND** follow-up requests for the replacement session do not receive a
  spurious bridge owner mismatch caused by the stale close

#### Scenario: detached retiring session releases resources after drain

- **GIVEN** a retiring bridge session was detached while visible requests were
  still draining
- **WHEN** those visible requests drain and the session is retired
- **THEN** the service releases the old session's durable ownership
- **AND** the service releases the old session's account lease
- **AND** upstream-reader-owned retirement does not self-cancel the current
  upstream reader task
- **AND** the detached session no longer holds bridge capacity until process
  exit

#### Scenario: LRU eviction releases lease before replacement account selection

- **GIVEN** the bridge is at local session capacity and an idle session is
  selected for LRU eviction
- **WHEN** a replacement bridge session is created after that eviction
- **THEN** the evicted session is closed through a bounded path before the
  replacement selects an account
- **AND** the evicted session's account lease does not cause the replacement to
  fail with a spurious no-account or local-capacity error

#### Scenario: cancellation during LRU close clears in-flight creation

- **GIVEN** the bridge is at local session capacity and an idle session is
  detached for LRU eviction before replacement creation
- **WHEN** the replacement request is cancelled while the bounded eviction close
  is still awaiting cleanup
- **THEN** the replacement in-flight creation marker is removed or failed before
  cancellation is propagated
- **AND** later requests for the same bridge key do not wait on that abandoned
  creation marker
