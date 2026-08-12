## Context

The HTTP Responses bridge multiplexes downstream requests over a reusable
upstream WebSocket. Recovery can be initiated either by the upstream reader or
by the downstream HTTP stream watchdog, so socket replacement, reader
ownership, pending-request settlement, and retry-circuit accounting cross
several asynchronous lifecycle boundaries. See `proposal.md` for motivation
and `specs/responses-api-compat/spec.md` for the normative contract.

Hard-affinity retry circuits are durable across replicas. Their evidence must
therefore describe a client-affecting request lifecycle, not merely a socket
lifecycle event, because idle socket retirement is normal bridge maintenance.

## Goals / Non-Goals

**Goals:**

- Transfer reader ownership atomically when a downstream watchdog replaces the
  upstream socket.
- Bound pre-visible recovery so it completes before the downstream client
  deadline without permitting duplicate visible work.
- Count only request-affecting, pre-response bridge failures toward the durable
  hard-key circuit.
- Preserve circuit state across replicas while bounding process-local and
  durable stale state.

**Non-Goals:**

- Replay work after any response event has become visible.
- Replay delivery-ambiguous liveness failures or continuity-sensitive payloads.
- Suppress a cooldown after two genuine consecutive eventless request
  failures.
- Change the Codex client's WebSocket-to-HTTP fallback policy.

## Decisions

### Treat the reader and socket as one generation

When recovery originates outside the reader, the bridge cancels and awaits the
old reader before locally closing its socket, keeps the shared session live
during replacement, and starts exactly one reader for the new socket. The old
reader's finalizer is generation-guarded so it cannot retire pending work that
has moved to the replacement.

Allowing old and new readers to overlap was rejected because a local close can
wake the old reader after the pending deque has already been transferred. A
simple `closed` flag was also rejected because it cannot distinguish the
superseded socket generation from the shared session lifetime.

### Keep pre-visible replay bounded and ahead of the client deadline

The bridge permits one additional clean-close replay only after the existing
first replay, only before any response event, and with bounded jitter. Silent
pre-response recovery starts after no more than six default ten-second
keepalive intervals, leaving headroom before a 120-second client deadline.

An unbounded reconnect loop was rejected because it can duplicate requests,
hide deterministic input rejection, and outlive the downstream caller.

### Derive circuit evidence from an owned request lifecycle

Retirement advances the circuit only when the retiring session still owns a
pending request and that lifecycle has observed zero response events. The
eligibility snapshot is taken while lifecycle ownership is known; an idle
session with no pending request remains visible in diagnostics but is neutral
to the circuit. A request that emitted any event is excluded because the
pre-response circuit cannot safely characterize a midstream failure.

Counting every socket retirement was rejected because routine idle churn
creates phantom first strikes. Counting only error labels was rejected because
the same transport label can describe idle maintenance, pre-response failure,
or midstream loss.

### Persist hard-key circuits and merge conservatively

Circuit rows are scoped by hard-affinity kind, key, and API-key scope. Conflict
updates cannot shorten an existing cooldown, retry decisions refresh durable
state, success clears state, and stale local/durable entries expire. Durable
lookup failures degrade to local state with diagnostics rather than failing the
request.

Process-local-only state was rejected because another replica could continue
replay during an open cooldown. Treating persistence failure as terminal was
rejected because the circuit is protective metadata, not request continuity
state.

### Judge stuck gates from upstream activity

The watchdog uses elapsed upstream inactivity plus the absence of a response
identifier or `response.created` latency. A prior continuity anchor receives a
bounded second threshold, not an indefinite exemption. Admission flags alone
were rejected because they can remain ambiguous while the upstream socket is
silent.

## Risks / Trade-offs

- [A replacement is also silent] -> The extra replay remains hard-capped and
  the request reaches terminal or circuit handling.
- [Reader cancellation races with pruning] -> Session handoff state keeps the
  shared lifecycle live until replacement ownership is established.
- [Concurrent replicas record failures] -> Durable merge semantics preserve
  the longest applicable cooldown.
- [A genuine failure occurs after an idle close] -> The idle close contributes
  no strike, so the genuine failure is correctly treated as the first one.
- [Database ancestry was stamped before a merge edge existed] -> A separate
  forward-only repair reconnects the request-usage rollup history without
  rewriting deployed migrations.

## Migration Plan

Apply the forward-only database revisions, deploy the revision-labelled image,
and verify bridge create/reuse, timeout, and retry-circuit diagnostics. Health
verification must confirm the expected image revision and current schema.
Rollback is an image replacement; the prior version can ignore the additional
runtime behavior while the durable circuit table and repair revision remain
forward-compatible.
