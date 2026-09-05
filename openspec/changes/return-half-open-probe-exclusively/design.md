## Context

The HTTP bridge retry circuit merges durable failure/cooldown rows into a
process-local monotonic state. Admission and upstream session teardown run in
the same process, but durable rows may be read by several replicas. The
implementation therefore needs a local lease owner without pretending that a
database row can identify the socket or pending attempts that own it.

See `proposal.md` for the motivation and `specs/responses-api-compat/spec.md`
for the normative behavior.

## Goals / Non-Goals

**Goals:**

- Keep elapsed durable deadlines at the existing `0.0` not-cooling sentinel.
- Make a real post-cooldown probe single-flight within one process and fence
  its return to the session that acquired it.
- Make explicitly identified proxy continuity recovery neutral to upstream
  failure accounting while preserving genuine eventless failure accounting.
- Make proxy-owned session reset atomic with respect to submit/teardown ownership,
  then perform potentially blocking settlement outside the lifecycle lock.

**Non-Goals:**

- No durable lease schema, cross-replica lock, attribution metadata, or new
  configuration.
- No session retirement for cooldown suppression (#1947), poison/quarantine
  behavior (#1891), denied-anchor retirement (#1902), or broad stale-anchor
  hardening (#1867).

## Decisions

### Normalize before merging

Convert a durable wall-clock deadline to a monotonic deadline only when the
remaining duration is positive. Otherwise merge `0.0`. This avoids creating a
synthetic monotonic deadline from a row that is already open for admission.
An absent, zero, or negative durable deadline is the unrestricted sentinel. A
newly adopted positive deadline that elapsed before the load instead records a
one-shot local transition: the first admission consumes it into the existing
owner-fenced half-open lease, and concurrent admissions remain suppressed until
that probe settles. Reloading the same durable snapshot does not re-arm the
transition. Equal-version reloads reconcile durable fields but do not clear a
live local half-open lease: the reload cannot prove which local session owns
it. A newer durable reset or lower failure count also preserves an active local
lease and its failure fence; only an inactive local state is cleared by a
durable reset. A lookup failure leaves the existing local state untouched.

### Fence a process-local lease by session, token, deadline, and generation

When admission crosses a real cooldown boundary, store the session identity,
owner token, deadline, and a monotonically increasing process-local lease
generation. Release requires an active lease and an exact match for every
available fence. A stale completion, including one reusing the same session and
token for a replacement lease, is a no-op. Release clears the lease and records
an elapsed local cooldown marker; it does not change durable failure fields. A
local release timestamp keeps that marker when a subsequent durable lookup
misses, so the next admission can still install exactly one fresh lease without
making a durable claim.

An owner request that remains pending after the default lease window is not an
abandoned probe when it has attempted `response.create` and has not entered
terminal settlement. Keep that owner exclusive and renew the lease through its
`bridge_request_deadline`. Completion settlement captures the durable episode
and local lease generation at admission; a generation mismatch is a no-op even
when a replacement probe reused the same session/token.

### Reuse the existing failure funnel for classification

Classify the established proxy continuity-loss details before the genuine
failure set. Continuity loss invokes the owner-fenced release and returns
without a durable write. A previous-response rejection is continuity-neutral
only when request state proves that the rejected anchor was proxy-injected;
raw/client-supplied rejection remains outside retry-circuit accounting.
Genuine `stream_incomplete`, `stream_idle_timeout`, and `clean_close` continue
through the existing attempt-scoped accounting path. Anchor replay and error
provenance remain owned by their existing vehicles.

### Serialize reset's critical section

The proxy-owned reset takes `session.lifecycle_lock`, detaches the session
under `_http_bridge_lock`, marks all pending response-create attempts disarmed,
and only then returns the half-open lease. It snapshots the work to settle and
closes the session after releasing the lifecycle lock. A shielded cleanup task
owns the critical transition so cancellation is reported to the caller only
after the registry and lease state are consistent. The required-owner
unavailable exit from an in-place reconnect uses the same lifecycle-ordered
detach and disarm transition before it releases the probe, then closes the
detached session through the same cancellation-deferred cleanup owner.

Account-lease release is part of detached-session ownership. A failed release
is retained on the detached session after transport close, and an explicit
account-scoped cleanup pass retries it. The session stays discoverable until
all retained handles clear; concurrent retry callers join one in-flight retry
task so they cannot double-release the same handle.

### Port only the accepted #1857 recovery exits

The mixin/protocol/streaming changes are limited to owner-unavailable and
proxy-owned reset paths from commits `d0075829`, `9a7dc342`, and `2a822b4f`.
No session-retirement, poison/quarantine, denied-anchor, attribution, or broad
anchor-recovery changes are copied from the overlapping vehicles.

## Risks / Trade-offs

- [Local leases are not replica-wide] → Keep durable failure/cooldown state as
  the shared authority and document that each process may admit its own probe
  after a real elapsed deadline.
- [A stale owner could attempt a late release] → Require an active lease and
  exact session, token, deadline, and generation; ignore non-owner releases.
- [Reset cancellation could strand resources] → Shield the detach/disarm/
  release transition and await the cleanup task before propagating cancellation.
- [Holding lifecycle ownership across network-like awaits could deadlock] →
  limit the lock to detach/disarm/release and settle/close afterwards.

## Migration Plan

No data migration is required. Deploying the code changes only local state
interpretation and recovery ordering. Rollback is a normal code rollback; the
durable failure/cooldown row remains compatible because no schema or serialized
field changes.
