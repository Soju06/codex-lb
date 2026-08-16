# responses-api-compat Delta

## ADDED Requirements

### Requirement: Scoped operation identity

The system MUST include the normalized API-key scope in every durable HTTP
bridge operation fingerprint and MUST apply that scope to fingerprint and
completed-operation lookups.

#### Scenario: Equal requests from different keys remain isolated

- **WHEN** two API keys submit the same logical request
- **THEN** each key receives an independent durable operation identity

### Requirement: Recoverable startup takeover

Startup cleanup MUST retain sessions that own submitted, acknowledged, or
unknown operations and MUST detach ownership before a replacement instance
takes over.

#### Scenario: Restart preserves an in-flight operation

- **WHEN** an instance restarts while an operation is nonterminal
- **THEN** cleanup detaches the old owner without deleting the operation spool

### Requirement: Fresh retry transcript

When an explicit failed operation is rebound, the system MUST atomically remove
the prior operation events and reset event-byte/spool state before accepting new
events.

#### Scenario: Failed retry cannot replay stale failure output

- **WHEN** a failed operation is retried and later completes
- **THEN** replay contains only the new attempt's events

### Requirement: Proof-gated sibling anchoring

The system MUST advance a continuation to a completed sibling response only
when the sibling has the same parent and logical request fingerprint in the
same API-key scope.

#### Scenario: Distinct sibling input keeps its requested parent

- **WHEN** a request reuses a parent with a different fingerprint
- **THEN** the service does not silently anchor it to another child response

### Requirement: Single migration head

The Alembic graph MUST converge the durable operation revisions with the current
release head and MUST expose one canonical head after upgrade.

#### Scenario: Upgrade resolves one head

- **WHEN** migrations are upgraded to the release tip
- **THEN** Alembic reports one canonical head

### Requirement: Conservative spool defaults

New operation rows MUST start with an incomplete event spool on SQLite and
PostgreSQL. A transcript MUST become replayable only after terminal event drain
and explicit finalization.

#### Scenario: Nonterminal spool is not replayable

- **WHEN** an operation has events but no finalized terminal event
- **THEN** recovery does not replay its transcript as complete

### Requirement: Retain completed recovery transcripts

Startup ownership cleanup MUST retain sessions with operation transcripts that
remain inside the configured operation retention window, including completed
operations, and MUST let normal spool retention remove the operation rows.

#### Scenario: Recent completed transcript survives takeover

- **WHEN** startup cleanup sees a recent completed transcript
- **THEN** it retains the session until normal retention expires it

### Requirement: Continuous transcript retention

Operation transcript cleanup MUST run periodically in a leader-gated scheduler
and MUST drain all eligible batches during each pass. Disabling the existing
sticky-session mapping cleanup switch MUST NOT disable operation transcript
retention; that switch MAY skip sticky mapping maintenance while durable
operation retention continues.

#### Scenario: Retention drains all eligible batches

- **WHEN** more rows are eligible than one deletion batch
- **THEN** one scheduler pass removes every eligible batch

#### Scenario: Sticky cleanup toggle does not disable transcript retention

- **WHEN** sticky-session cleanup is disabled and the durable bridge schema is
  available
- **THEN** the leader-gated scheduler still drains expired operation transcript
  rows while skipping sticky mapping cleanup

### Requirement: Fresh indefinite-recovery spool

Before dispatching a server-owned retry for a nonterminal operation, the system
MUST atomically clear any partial event spool under the durable owner fence.

#### Scenario: Retry starts with a clean transcript

- **WHEN** an anchored retry is dispatched after partial persistence
- **THEN** old events and byte counts are cleared before new output is accepted

### Requirement: Ordered deferred reasoning persistence

Deferred reasoning events released before a visible event MUST be persisted in
the same order in which they are delivered downstream, before the visible
event is persisted.

#### Scenario: Deferred events preserve downstream order

- **WHEN** buffered reasoning is released before visible output
- **THEN** the durable spool stores the reasoning blocks before that output

### Requirement: Per-operation disconnect classification

When a shared bridge websocket closes, each pending operation MUST be
classified from that operation's own observed response-event count. Activity
from a sibling request MUST NOT make an eventless operation safely retryable.

#### Scenario: Sibling output does not acknowledge an eventless request

- **WHEN** one pending request emitted output and another emitted none
- **THEN** the two operations receive different disconnect classifications

### Requirement: Abandoned operation retention

Operation retention MUST expire stale submitted and acknowledged rows in
addition to terminal and ambiguous rows, so a crashed or abandoned operation
cannot retain raw request data indefinitely.

#### Scenario: Stale abandoned request is purged

- **WHEN** a submitted operation exceeds retention age
- **THEN** its request data and event spool are removed

### Requirement: Acknowledged alias persistence failure

If upstream has acknowledged a response but local continuity-alias persistence
fails, the downstream error MUST NOT transition the durable operation to a
retryable failed state. The operation MUST remain acknowledged/ambiguous so an
identical retry cannot dispatch a duplicate upstream turn.

#### Scenario: Alias write failure remains fail-closed

- **WHEN** an acknowledged response cannot publish its continuity alias
- **THEN** the operation remains non-retryable and the client receives a terminal error

### Requirement: Cross-session nonterminal handoff

When a scoped operation fingerprint is found under a different durable
session, a nonterminal operation MUST be atomically rebound to the currently
owned session before its event spool is reset or a recovery attempt is sent.
Completed replayable operations MUST remain attached to their original session.
The handoff MUST be refused while the prior session has an unexpired owner
lease, preventing concurrent owners from dispatching the same turn.

#### Scenario: Active prior owner fences handoff

- **WHEN** a duplicate request finds a nonterminal operation under another session
- **AND** that session still has an unexpired owner lease
- **THEN** the operation remains with the prior session and no concurrent retry is dispatched

#### Scenario: Expired prior owner permits handoff

- **WHEN** the prior session lease is absent or expired
- **THEN** the operation can be atomically rebound before recovery

### Requirement: Fenced one-shot recovery dispatch

The durable recovery journal MUST persist a one-shot replay budget for every
recovery-safe request. The budget MUST be consumed atomically when a replay is
claimed for dispatch, and a caller that proves the replay never reached the
upstream send boundary MUST restore that claim under the same session owner
fence. A replacement session MUST retain or transfer a fenced origin owner
until the claim is rolled back or settled; selecting a replacement or failing
preflight MUST NOT permanently consume an unsent replay.

#### Scenario: Concurrent reconnects consume one replay

- **WHEN** concurrent reconnects observe the same ambiguous operation
- **THEN** exactly one owner atomically claims the persisted replay budget and
  other reconnects fail closed without dispatching a duplicate

#### Scenario: Pre-dispatch replacement failure restores the budget

- **WHEN** a replay claim is made but replacement admission or preflight fails
  before the exact upstream frame is sent
- **THEN** the claim returns to the available state and the fenced origin
  owner is released only after that rollback succeeds

#### Scenario: Successful replacement settles the origin journal

- **WHEN** a replacement session dispatches the claimed replay and receives a
  terminal response event
- **THEN** settlement uses the retained origin owner fence before releasing it
  and the replay budget cannot be claimed again

### Requirement: Lease-aware operation retention

Retention MUST NOT delete stale submitted or acknowledged operations while
their session is actively owned with an unexpired lease. The owner/lease
predicate MUST be rechecked in the deletion transaction.

#### Scenario: Active lease protects stale operation

- **WHEN** a stale operation belongs to a session with a live lease
- **THEN** retention leaves it intact

### Requirement: Anchored indefinite recovery gate

The server-indefinite recovery loop MUST be installed only for an eventless
anchored continuation with a durable parent operation. Fresh first-turn
requests and streams that already emitted downstream response events MUST
terminate normally rather than being resent indefinitely.

#### Scenario: Fresh request is not held indefinitely

- **WHEN** a first-turn request loses its upstream connection
- **THEN** the proxy returns its normal error path without an indefinite loop

### Requirement: Retry reservation terminalization

If reacquiring API-key usage limits for a recovery attempt fails, the proxy
MUST settle the prior reservation and emit a terminal `response.failed` SSE
event instead of aborting the already-started stream.

#### Scenario: Quota failure produces terminal SSE

- **WHEN** a recovery retry cannot reacquire its usage reservation
- **THEN** the client receives `response.failed` and the prior reservation is settled

#### Scenario: Unexpected admission failure produces terminal SSE

- **WHEN** recovery admission raises an unexpected infrastructure error before
  a replacement stream starts
- **THEN** the client receives `response.failed` and the prior reservation is
  settled instead of receiving a truncated stream

### Requirement: Failure spool/state ordering

For an explicit deterministic failure, the proxy MUST persist the terminal SSE
block before exposing the durable operation as failed. The event append and
failed-state transition MUST use the same owner fence and transaction when the
durable repository supports it.

#### Scenario: Concurrent retry cannot reset an unspooled failure

- **WHEN** a response failure is being settled while an identical reconnect is
  admitted
- **THEN** the reconnect observes the terminal operation fence and cannot reset
  or mix the previous failure into a new transcript

### Requirement: Partial disconnect acknowledgement

When a bridge disconnects after an operation has emitted any response event but
before a terminal event, the durable operation MUST remain acknowledged or
ambiguous. It MUST NOT be classified as retryable failed solely because the
disconnect was non-terminal.

#### Scenario: Partial output is never resent as a fresh turn

- **WHEN** the upstream closes after `response.created` but before completion
- **THEN** the operation remains non-retryable

### Requirement: Retry output stops indefinite recovery

An indefinite recovery attempt MUST stop retrying once that attempt emits any
downstream response event, even if the attempt later fails with a retryable
transport error.

#### Scenario: Retry output prevents a second attempt

- **WHEN** a retry emits a data event and then times out
- **THEN** the server stops the indefinite loop instead of appending another response

### Requirement: Preserve repeated event occurrences

The durable event spool MUST preserve repeated identical SSE blocks as distinct
ordered occurrences. Event identity MUST include its operation-local sequence
position rather than content alone.

#### Scenario: Identical deltas replay twice

- **WHEN** two consecutive SSE blocks have identical text
- **THEN** both occurrences are present in the replay transcript

### Requirement: Stop event persistence during shutdown

Proxy shutdown MUST close the HTTP bridge event batcher and cancel its
background flusher before the process exits.

#### Scenario: Shutdown cancels the flusher

- **WHEN** the proxy service begins shutdown after queueing an event
- **THEN** the batcher's background task is cancelled and awaited

### Requirement: Classify response.incomplete as terminal

An anchored `response.incomplete` event MUST transition the durable operation to
an explicit terminal state and finalize its transcript so it is not left in an
unknown in-flight state.

#### Scenario: Incomplete response is replayable as terminal

- **WHEN** upstream emits `response.incomplete`
- **THEN** the operation is terminalized and its drained transcript is eligible for replay

### Requirement: Settle reservations before timeout health

When an eventless timeout retires a keyed bridge, the proxy MUST settle all
pending request reservations before recording the account timeout health signal.
If settlement fails, the health signal MUST NOT claim that cleanup completed.

#### Scenario: Failed reservation release does not poison health state

- **WHEN** the timeout cleanup cannot release a pending reservation
- **THEN** the account timeout signal is not recorded before that failure is surfaced

### Requirement: Replay finalized incomplete operations

A finalized `incomplete` operation transcript MUST be replayed for an identical
request and MUST NOT be reset or treated as an unknown in-flight operation.

#### Scenario: Reconnect receives stored incomplete transcript

- **WHEN** an identical request finds a finalized incomplete operation
- **THEN** the stored terminal transcript is delivered without a new upstream dispatch

### Requirement: Validate final response.create size

After adding durable operation metadata, the proxy MUST revalidate the exact
serialized `response.create` frame against the upstream size limit before
sending it.

#### Scenario: Metadata cannot create an oversized frame

- **WHEN** operation metadata makes the final frame exceed the configured limit
- **THEN** the request is rejected or slimmed before any upstream send

### Requirement: Fence same-session active operations

Server-indefinite recovery MUST NOT reset or redispatch a nonterminal operation
when another pending request in the same durable session still references that
operation. Submitted and acknowledged operations MUST remain fail-closed;
only an inactive `unknown` operation may enter a fresh recovery attempt.

#### Scenario: Active same-session operation is not duplicated

- **WHEN** a duplicate request finds a submitted operation still referenced by another pending request
- **THEN** the proxy refuses a second dispatch and preserves the existing spool
