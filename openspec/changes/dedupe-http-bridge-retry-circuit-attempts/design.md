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
and `retry_circuit_failure_recorded` state. It also carries a settlement signal,
but deliberately does not cache a historical failure count. Observers capture
the object itself, not merely the request's current ordinal.

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

If the failure was already recorded, later duplicate observers wait for its
durable merge to settle and then read the live circuit state without another
increment. This means a later independent attempt is reflected in the returned
count, while a successful response that cleared the circuit is reported as
zero. It preserves the reader's existing threshold-dependent durable-anchor
handling without allowing a cached count from an older send to reopen or poison
state that has since changed.

### Distinguish absent attribution from ambiguous attribution

Failure funnels pass an explicit selection result rather than overloading
`None`. `absent` means the legacy path has no attempt object and may use the
unscoped recorder. `eligible`, `recorded`, and `settled` retain the exact object
identities, including multiple candidates. `ineligible` means an attempt was
present but lifecycle evidence makes it unsafe to charge.

A single candidate is handled with the normal attempt-scoped recorder. Multiple
eligible or settled candidates are deliberately suppressed rather than falling
back to an unscoped strike, because an unscoped increment cannot identify which
physical send it represents and can double-count a later observer. Multiple
already-recorded candidates wait for settlement and return the live circuit
count without incrementing.

### Claim under the existing retry-circuit lock

The attempt marker and `consecutive_failures` increment are changed in the same
critical section guarded by `_http_bridge_retry_circuit_lock`. Durable I/O stays
outside that lock. Duplicate calls may both perform the existing durable load,
but only the first claim persists a failure.

No new lock is introduced. Failure paths release `pending_lock` before entering
the recorder, and no retry-circuit path acquires `pending_lock` or
`lifecycle_lock` while holding the retry-circuit lock.

### Capture before ownership and recovery awaits

The response-create gate classifies stale owners and snapshots their attempts
while holding `pending_lock`. Shared cleanup also snapshots before it waits to
acquire that lock, so callers that do not provide a locked snapshot still retain
the pre-wait identity. The downstream timeout and reader watchdog likewise
capture before calling retry, reconnect, receive cancellation, or settlement
helpers. Reading the request state's current attempt afterward could attribute
an old timeout to a newer retry or suppress the old failure incorrectly.

### Mark lifecycle observation before deferred delivery

Some reasoning prelude events are intentionally deferred and therefore do not
increment the ordinary response-event counter. They still prove that upstream
accepted and began answering the physical `response.create`. The matched
attempt is marked observed immediately, before deferred-delivery branching or
any later await, while the existing event-count and downstream-visibility
semantics remain unchanged.

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
  observer from recreating the cleared failure and the live count returned to
  it is zero.
- If a cleanup snapshot contains multiple eligible sends, it records none at
  that ambiguous boundary. Later observers that retain an exact send identity
  can still claim each genuine failure independently.
- If response accounting is deferred for a reasoning prelude, the attempt's
  observed marker still wins against an eventless timeout without making the
  deferred event visible or incrementing its ordinary event count.

## Example

Attempt A is sent and remains eventless. The downstream stream watchdog and the
reader watchdog both capture A. The downstream task claims A first, records
failure count 1, and persists once. The reader later sees that A is already
recorded, waits for settlement, reads the live count, and does not persist. If
recovery sends attempt B and B also fails before that reader resumes, the reader
returns the current count 2 without adding a third strike. If a successful
response clears the state instead, it returns 0.
