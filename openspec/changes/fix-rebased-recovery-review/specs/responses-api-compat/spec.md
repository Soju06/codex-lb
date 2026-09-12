## ADDED Requirements

### Requirement: Live transcript capture is bounded before completion

The bridge MUST enforce its transcript byte and item limits while accepting output item
events, including added-item identities. Crossing the limit MUST release captured
items and disable reconstruction for that attempt without failing live delivery.
Replay MUST reset that accounting before accepting replacement output.

#### Scenario: Output exceeds the bound before a terminal event

- **WHEN** accumulated output item events exceed the configured transcript bound
- **THEN** capture releases its items and identities immediately
- **AND** later events cannot re-enable that attempt's transcript

### Requirement: Optional snapshots do not block live readers

Terminal snapshot reconstruction MUST run after delivery in a bounded,
service-owned background task with captured ownership and generation fences.
Pre-delivery operation updates MUST NOT reconstruct transcripts. Snapshot tasks
MUST have a bounded backlog and deadline and participate in graceful shutdown.
Graceful shutdown MUST drain these tasks before releasing bridge owner leases.

#### Scenario: Shutdown begins with a pending snapshot

- **WHEN** a completed response has a pending detached snapshot
- **THEN** the pre-teardown persistence drain awaits it while bridge ownership is held
- **AND** only afterward may bridge teardown release the owner lease

#### Scenario: Parent transcript lookup stalls

- **WHEN** optional snapshot reconstruction waits on a parent lookup
- **THEN** the terminal and reader remain available without waiting for that lookup
- **AND** expiry or cancellation leaves the operation ineligible for snapshot replay

### Requirement: Transcript batch cleanup respects ownership

Failed transcript writes MUST discard pending work only while the failed batch
still owns the current generation and session ownership fence. Abandoned
contextless recovery fences MUST release their bookkeeping after bounded retention.

#### Scenario: Ownership changes during a failed append

- **WHEN** a successor rebinds ownership while an older batch is being persisted
- **THEN** rejection or failure of that older batch MUST NOT discard successor events

#### Scenario: A recovery fence is abandoned before enqueue

- **WHEN** no replacement context or event arrives within the retention window
- **THEN** generation and operation-lock bookkeeping are released
- **AND** superseded contexts from the previous attempt do not prevent cleanup

#### Scenario: Cancellation resolves an ordinary append outcome

- **WHEN** shutdown cancellation arrives while a dequeued ordinary batch is
  being appended durably
- **THEN** the batcher MUST await the append outcome before deciding whether to
  restore the batch
- **AND** a successfully committed batch MUST NOT be requeued or persisted a
  second time

#### Scenario: Failed append bookkeeping survives cancellation

- **WHEN** a durable append reports failure and cancellation arrives while the
  batcher's failure marker is being recorded
- **THEN** the failed-batch marker MUST be recorded before the batch is cleared
- **AND** a later terminal drain MUST NOT finalize that operation as replayable

#### Scenario: Close drains a restored ordinary batch

- **WHEN** a flusher cancellation restores a dequeued ordinary batch to the
  in-memory queue
- **THEN** `close()` MUST drain that batch through the durable writer before it
  returns
- **AND** close MUST NOT leave the operation's pending counters or event data
  stranded in memory
- **AND** any shutdown deadline MUST bound the flusher and pending-drain waits
  before forced termination

### Requirement: Normalizable text extensions retain streaming parity

Public Responses MUST preserve successful completion when an upstream text-bearing
output extension can be normalized into a public message. Streaming item events,
terminal-only output, and collected responses MUST use equivalent normalization.
Terminal echoes MUST agree with normalized streamed output, including stable item
identities and content. Opaque items and contradictory output MUST still fail.

#### Scenario: A text extension precedes completion

- **WHEN** a final-answer text item is emitted as a done-only or added/done lifecycle
- **THEN** streamed and collected responses complete with the normalized message
- **AND** an equivalent terminal echo or empty output backfill preserves that result

### Requirement: Replays start with fresh output validation state

Every admitted WebSocket replay MUST clear prior-attempt output items, indexes,
identities, added tool-call IDs, and output validation/completion flags before
processing the replacement response. A rejected replay MUST preserve that state.
Replay authorization, ownership, and downstream lifecycle suppression MUST retain
their existing behavior.

#### Scenario: Capacity or stale-anchor replay replaces an attempt

- **WHEN** a capacity retry is staged or a verified fresh replay body is installed
- **THEN** valid replacement output MUST NOT inherit an invalid-output flag or item identity from the previous attempt

#### Scenario: Replay admission is rejected

- **WHEN** a replay gate or fresh-body validation rejects the retry
- **THEN** the current attempt's output validation state remains unchanged

### Requirement: Transcript row selection is deterministic

Complete-transcript lookup MUST prefer the most recently updated completed
operation for a response ID and use ascending operation ID to break timestamp
ties, independent of physical insertion order.

#### Scenario: Duplicate response IDs have equal timestamps

- **WHEN** completed operations share a response ID and update timestamp but different parents
- **THEN** lookup selects the operation with the smallest operation ID and follows its parent chain

### Requirement: Reconnect-only recovery preserves replay authorization

The bridge MUST preserve complete-transcript and unsafe-partial one-shot replay
authorization when reconnecting without dispatching a replay. An actual fresh
replay attempt MUST consume its authorization before reconnecting, without
resetting the ordinary replay counter.

#### Scenario: Reconnect before authorized replay

- **WHEN** an authorized request reconnects with request sending disabled
- **THEN** no replay is dispatched and its authorization remains available
- **AND** a subsequent fresh replay consumes that authorization exactly once
- **AND** reconnect-only handling MUST NOT claim the stale-anchor circuit dispatch generation

#### Scenario: Parked admission returns zero cooldown

- **WHEN** admission repeatedly parks a request with zero retry delay
- **THEN** each park MUST await a positive bounded interval without exceeding the remaining request budget
- **AND** renewed admission remains authoritative after that wait

### Requirement: Root UNKNOWN replay is validated before admission

The bridge MUST validate a self-contained, account-neutral root and apply
configured replay bounds before authorizing UNKNOWN recovery. It MUST dispatch
the sanitized root, not the original client body. Invalid roots MUST NOT
authorize a recovery claim.

#### Scenario: Unsafe client history

- **WHEN** a root contains account-scoped content or unsettled tool history
- **THEN** it cannot authorize UNKNOWN replay

#### Scenario: Valid root contains response-owned fields

- **WHEN** an eligible root is admitted for UNKNOWN recovery
- **THEN** response-owned fields are sanitized before dispatch

#### Scenario: Sanitized retry retains its durable identity

- **WHEN** the original root body contains response-owned fields and its operation is UNKNOWN
- **THEN** the bridge MUST derive the lookup and recorded operation fingerprint from the original body
- **AND** dispatch of the sanitized body MUST require claiming that same UNKNOWN operation
- **AND** a rejected claim MUST prevent upstream dispatch

#### Scenario: A prior version recorded a sanitized root

- **WHEN** the original root fingerprint has no matching operation but a prior version recorded the sanitized root
- **THEN** the bridge MUST reuse that operation's durable identity before recording or claiming recovery
- **AND** rejection of its recovery claim MUST prevent dispatch

#### Scenario: Soft affinity has no verified durable account owner

- **WHEN** a root request has a sticky turn-state but no preferred durable account
- **THEN** non-neutral recovery MUST NOT arm the durable predecessor anchor
- **AND** explicit account-neutral replay MUST retain its existing recovery contract

### Requirement: Interrupted output preserves explicit terminal errors

A non-streaming Responses collector MUST preserve an explicit upstream error
when otherwise valid output items have not finished. Successful completion MUST
still reject unfinished items, and malformed item identities MUST remain errors.

#### Scenario: Failure during a partial item

- **WHEN** an added output item is followed by error or response.failed
- **THEN** the original terminal error code and message are returned

### Requirement: Supported tools do not legitimize opaque output

Public Responses MUST preserve supported tool-search output, but MUST NOT
return success after discarding an opaque unknown output item. This applies to
both streamed and collected responses.

#### Scenario: Tool-search response also contains unknown output

- **WHEN** otherwise supported tool-search output includes an opaque unknown item
- **THEN** the response ends with invalid_output_item rather than partial success

#### Scenario: Terminal-only output contains an unknown item

- **WHEN** a terminal payload contains supported and opaque output without prior item events
- **THEN** streamed and collected public responses MUST fail with invalid_output_item

### Requirement: Native streams discard parsed data after completion

Native Responses streams MUST discard parsed vendor and response data after the
first terminal event, while preserving comments and the DONE sentinel.

#### Scenario: Vendor metadata arrives after completion

- **WHEN** a native upstream sends vendor metadata both before and after completion
- **THEN** only the metadata before completion is forwarded
- **AND** trailing comments and the DONE sentinel remain available
