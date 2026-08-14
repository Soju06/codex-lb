# Design: attempt-scoped retry-circuit recording

## Context

All HTTP bridge upstream sends pass through
`_send_http_bridge_request_text_with_archive_id`, but retry-circuit failures can
be reported by four paths: partial stale cleanup, direct stale retirement, the
upstream reader failure funnel, and downstream stream-idle handling. These
paths may run concurrently and may await recovery or settlement before they
record the failure.

The durable retry-circuit upsert intentionally merges separate writes as
separate observations. It cannot infer that two writes came from the same
physical send, and adding a durable attempt key would expand the schema and the
rolling-upgrade contract unnecessarily.

## Decisions

### Keep identity process-local and object-scoped

Each upstream send creates a new attempt object stored on its request state.
The object carries a diagnostic ordinal plus `disarmed`, `response_observed`,
and `retry_circuit_failure_recorded` state. Observers capture the object itself,
not merely the request's current ordinal.

An older observer therefore retains the identity of the send it classified even
if a retry replaces the request state's current attempt. The old object remains
alive only while an observer references it, so no unbounded generation set is
needed.

### Preserve the existing failure eligibility contract

Creating an attempt does not itself record a failure. A send exception or
cancellation disarms it using the same cleanup boundary that clears
`response_create_sent_at`. A matched `response.*` event marks it observed before
the reader performs another await. An observer that has not already recorded
the attempt must not record it after either condition wins.

If the failure was already recorded, later duplicate observers receive the
current circuit count without another increment. This preserves the reader's
existing threshold-dependent durable-anchor handling even when the downstream
stream was the first observer.

### Claim under the existing retry-circuit lock

The attempt marker and `consecutive_failures` increment are changed in the same
critical section guarded by `_http_bridge_retry_circuit_lock`. Durable I/O stays
outside that lock. Duplicate calls may both perform the existing durable load,
but only the first claim persists a failure.

No new lock is introduced. Failure paths release `pending_lock` before entering
the recorder, and no retry-circuit path acquires `pending_lock` or
`lifecycle_lock` while holding the retry-circuit lock.

### Capture before recovery awaits

The downstream timeout and reader watchdog capture the classified attempt before
calling retry, reconnect, receive cancellation, or settlement helpers. Reading
the request state's current attempt afterward could attribute an old timeout to
a newer retry or suppress the old failure incorrectly.

### Keep replica behavior unchanged

The active owner alone holds the upstream WebSocket and its request state, so
duplicate local observers share one attempt object. Owner forwarding does not
create another upstream send on the forwarding replica. A replay after owner
handoff is a new send and is intentionally a new strike. Existing durable
conflict merging continues to combine genuinely independent replica failures.

## Failure Modes

- If durable lookup or persistence fails, the first claim remains in local
  circuit state as it does today; a duplicate observer must not retry the write
  because the durable upsert would interpret it as a second failure.
- If a response event wins before the first claim, the attempt is not counted.
  If a failure claim wins first, a later response cannot turn a duplicate
  observer into another strike.
- If a successful terminal response clears the circuit before a delayed
  duplicate observer resumes, the retained attempt marker prevents the old
  observer from recreating the cleared failure.

## Example

Attempt A is sent and remains eventless. The downstream stream watchdog and the
reader watchdog both capture A. The downstream task claims A first, records
failure count 1, and persists once. The reader later sees that A is already
recorded, returns count 1, and does not persist. If recovery sends attempt B and
B also fails, B is claimed independently and opens the circuit at count 2.
