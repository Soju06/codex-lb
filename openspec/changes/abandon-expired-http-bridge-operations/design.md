## Context

`http_bridge_operations` is a durable duplicate-suppression ledger. A
transport failure after dispatch marks the operation `unknown` when no
response lifecycle event was observed, or `acknowledged` when upstream sent a
response event but no terminal outcome was persisted. Neither state proves
that upstream did not execute the request, so normal recovery must remain
fail-closed and must not silently resend it.

The bridge already has two useful safety signals:

1. the HTTP bridge request budget bounds the lifetime of a request that can
   still be actively using the operation; and
2. the local bridge registry can identify operation IDs still held by pending
   request states, including detached generations during settlement.

The new cleanup uses both signals. A database owner lease is also checked so
one replica cannot abandon work that another replica still owns. An owned row
must remain expired for one additional durable lease period before it is
eligible, so a brief renewal blip on one replica cannot let another replica
fence an active finalization path. The sweep is therefore conservative: it may
leave a stale row until the next pass, but it cannot turn a live owner or
pending request into a retryable duplicate.

The pending-operation protection snapshot is bounded before it is rendered as
an expanding database predicate. If the snapshot exceeds the repository's
database-safe bind limit, the sweep reads a finite candidate slice and filters
the full protection set in memory rather than truncating it. The coordinator
keeps the returned `(updated_at, operation_id)` keyset cursor and supplies it
to the next heartbeat, wrapping back to the beginning after the eligible
range is exhausted. This preserves every protected operation while allowing
unrelated stale rows to converge without holding the SQLite writer section for
an unbounded scan. The cursor is best-effort on SQLite: a row whose
`updated_at` shares the cursor row's second but was stamped in a different
text form may be skipped for one wrap and is picked up when the scan restarts.

## Goals / Non-Goals

**Goals:**

- Bound the lifetime of an ambiguous operation after its request budget and
  owner proof have expired.
- Preserve the durable row and all existing event history for audit and normal
  retention.
- Make the state transition atomic and resistant to late transport/status
  callbacks and concurrent recovery claims.
- Give a later continuation a deterministic, standard full-history recovery
  signal.
- Keep the sweep bounded and off the request path.

**Non-Goals:**

- Prove whether ChatGPT executed an operation after the upstream connection
  became ambiguous; no such status endpoint exists in this path.
- Automatically replay, cancel, or compensate the ambiguous operation.
- Abandon a row with a live session owner lease or a locally pending request.
- Change the existing 7-day transcript retention or purge rows as an incident
  workaround.
- Change account stream caps or other capacity policy.

## Decisions

### 1. Use an explicit terminal state

`abandoned` is terminal for duplicate suppression and recovery admission, but
it is not relabeled `failed`: the proxy cannot prove that upstream failed.
Completed/incomplete/failed behavior remains unchanged. Existing
`record_operation`, reset, and append paths treat `abandoned` as immutable;
late operation writes are fenced out. This covers both spool formats: the
`rows_v1` writers check the state under their owner fence, and the shared
`chunks_v2` lock helper refuses an `abandoned` row before any chunk append or
terminal settlement can touch `state`, `response_id`, or the spool.

### 2. Derive the cutoff from the existing request budget

The sweep cutoff is `now - max(1800 seconds,
http_responses_session_bridge_request_budget_seconds)`. This keeps the
mechanism zero-config and ensures that a request cannot be abandoned merely
because the operator selected a short bridge budget. The operation's
`updated_at` is the inactivity clock; any persisted event or status proof
refreshes it and causes the candidate to be reconsidered.

### 3. Protect active local work and live durable owners

The service snapshots operation IDs from both canonical and detached local
bridge generations while holding the bridge lock. The repository sweep skips
those IDs. For all other candidates it locks the operation and owning session,
then requires:

- state is still `unknown` or `acknowledged`;
- `updated_at` is still older than the inactivity cutoff (compared against the
  cutoff rather than for equality with the loaded value: on SQLite the
  `onupdate` timestamp written by the event appenders is second-precision text
  while the loaded datetime binds back with microseconds, so an equality
  predicate never matches the acknowledged rows that streamed at least one
  event before their transport was lost);
- the owner instance and epoch still equal the candidate values; and
- the session has no owner or its lease is expired.

An owned session's lease must have been expired for at least one full durable
lease period. This cross-replica grace protects a still-running owner from a
single renewal blip while preserving eventual convergence after owner loss.
The same grace applies after `release_session` clears the owner and records
`lease_expires_at=now`, because the releasing replica may still be finalizing
pending work. PostgreSQL retains the operation/session row lock on both the
normal predicate path and the oversized-protection bounded-page path.

The final update is a single conditional update inside the write transaction.
The state check prevents a late `update_operation` or recovery claim from
reviving an already abandoned operation.

When the protection snapshot is oversized, the repository inspects no more
than its finite scan budget in one sweep and returns a keyset cursor for the
next slice. The coordinator preserves that cursor across heartbeats and
resets it only after the eligible range is exhausted or the bounded path is
no longer needed.

### 4. Return the standard continuity error

When operation admission finds `abandoned`, it never calls
`claim_unknown_operation_for_recovery` and never sends upstream. It returns a
400 `previous_response_not_found` error with `previous_response_id` as the
parameter. This is the same public contract used for a dead durable owner and
lets Codex construct a safe full-history retry; it does not require the proxy
to guess whether the original ambiguous request was accepted.

A hard turn-state operation can be fenced without carrying
`previous_response_id`. For that shape the error keeps the canonical
`previous_response_not_found` code but omits the inapplicable parameter and
explicitly requests a full-history retry, causing Codex to discard the hard
continuity anchor instead of repeating the abandoned fingerprint.

### 5. Observe only low-cardinality abandonment metadata

The sweep logs the operation state/reason, hashed session identity, owner
status, and age, and increments a counter labeled only by source state. Raw
request text, response IDs, API keys, and account emails are not included.

## Race handling

- A current owner renewing or claiming a row changes the session epoch or
  stamps the operation `updated_at` at roughly now, which is never older than
  the cutoff; the compare-and-set then affects zero rows. If a
  renewal is briefly delayed, the additional lease-period grace keeps the row
  ineligible before the compare-and-set is attempted.
- A nonterminal status event that commits after candidate selection advances
  the operation's durable `event_bytes` progress; the compare-and-set also
  compares that progress. The current ORM append paths refresh `updated_at`,
  while the independent progress comparison remains a second fence for any
  competing writer whose timestamp update is coalesced or omitted.
- A concurrent recovery claim changes `unknown` to `submitted`; the
  `unknown`/`acknowledged` predicate rejects abandonment.
- A late upstream event after abandonment cannot update the row because all
  operation state writers reject terminal `abandoned` rows. The sweep leaves
  session ownership in place, so this fence must not rely on the owner
  instance/epoch check alone: a lease-expired owner still matches it.
- A request that is already pending locally is protected by the service
  snapshot, including detached generations that are still draining.

## Example

An eventless operation is marked `unknown` at 12:00. Its request budget is two
hours, so it is not eligible before 14:00. At 14:05 the bridge heartbeat sees
no local pending request and the durable session lease has expired. The CAS
moves it to `abandoned` while keeping its event rows. The next Codex
continuation receives `previous_response_not_found`; the proxy performs no
second `response.create`, and Codex can resend its full local history as a new
operation.
