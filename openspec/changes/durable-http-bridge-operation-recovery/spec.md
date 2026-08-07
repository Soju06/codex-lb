## Requirements

### Requirement: Scoped operation identity

The system MUST include the normalized API-key scope in every durable HTTP
bridge operation fingerprint and MUST apply that scope to fingerprint and
completed-operation lookups.

### Requirement: Recoverable startup takeover

Startup cleanup MUST retain sessions that own submitted, acknowledged, or
unknown operations and MUST detach ownership before a replacement instance
takes over.

### Requirement: Fresh retry transcript

When an explicit failed operation is rebound, the system MUST atomically remove
the prior operation events and reset event-byte/spool state before accepting new
events.

### Requirement: Proof-gated sibling anchoring

The system MUST advance a continuation to a completed sibling response only
when the sibling has the same parent and logical request fingerprint in the
same API-key scope.

### Requirement: Single migration head

The Alembic graph MUST converge the durable operation revisions with the current
release head and MUST expose one canonical head after upgrade.

### Requirement: Conservative spool defaults

New operation rows MUST start with an incomplete event spool on SQLite and
PostgreSQL. A transcript MUST become replayable only after terminal event
drain and explicit finalization.

### Requirement: Retain completed recovery transcripts

Startup ownership cleanup MUST retain sessions with operation transcripts that
remain inside the configured operation retention window, including completed
operations, and MUST let normal spool retention remove the operation rows.

### Requirement: Continuous transcript retention

Operation transcript cleanup MUST run periodically in a leader-gated scheduler
and MUST drain all eligible batches during each pass.

### Requirement: Fresh indefinite-recovery spool

Before dispatching a server-owned retry for a nonterminal operation, the system
MUST atomically clear any partial event spool under the durable owner fence.

### Requirement: Ordered deferred reasoning persistence

Deferred reasoning events released before a visible event MUST be persisted in
the same order in which they are delivered downstream, before the visible
event is persisted.

### Requirement: Per-operation disconnect classification

When a shared bridge websocket closes, each pending operation MUST be
classified from that operation's own observed response-event count. Activity
from a sibling request MUST NOT make an eventless operation safely retryable.

### Requirement: Abandoned operation retention

Operation retention MUST expire stale submitted and acknowledged rows in
addition to terminal and ambiguous rows, so a crashed or abandoned operation
cannot retain raw request data indefinitely.

### Requirement: Acknowledged alias persistence failure

If upstream has acknowledged a response but local continuity-alias persistence
fails, the downstream error MUST NOT transition the durable operation to a
retryable failed state. The operation MUST remain acknowledged/ambiguous so an
identical retry cannot dispatch a duplicate upstream turn.

### Requirement: Cross-session nonterminal handoff

When a scoped operation fingerprint is found under a different durable
session, a nonterminal operation MUST be atomically rebound to the currently
owned session before its event spool is reset or a recovery attempt is sent.
Completed replayable operations MUST remain attached to their original
session.

### Requirement: Lease-aware operation retention

Retention MUST NOT delete stale submitted or acknowledged operations while
their session is actively owned with an unexpired lease. The owner/lease
predicate MUST be rechecked in the deletion transaction.

### Requirement: Anchored indefinite recovery gate

The server-indefinite recovery loop MUST be installed only for an eventless
anchored continuation with a durable parent operation. Fresh first-turn
requests and streams that already emitted downstream response events MUST
terminate normally rather than being resent indefinitely.

### Requirement: Retry reservation terminalization

If reacquiring API-key usage limits for a recovery attempt fails, the proxy
MUST settle the prior reservation and emit a terminal `response.failed` SSE
event instead of aborting the already-started stream.

### Requirement: Partial disconnect acknowledgement

When a bridge disconnects after an operation has emitted any response event but
before a terminal event, the durable operation MUST remain acknowledged or
ambiguous. It MUST NOT be classified as retryable failed solely because the
disconnect was non-terminal.

### Requirement: Retry output stops indefinite recovery

An indefinite recovery attempt MUST stop retrying once that attempt emits any
downstream response event, even if the attempt later fails with a retryable
transport error.

### Requirement: Preserve repeated event occurrences

The durable event spool MUST preserve repeated identical SSE blocks as distinct
ordered occurrences. Event identity MUST include its operation-local sequence
position rather than content alone.

### Requirement: Stop event persistence during shutdown

Proxy shutdown MUST close the HTTP bridge event batcher and cancel its
background flusher before the process exits.
