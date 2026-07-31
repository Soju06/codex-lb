## Context

`ApiKeysService.enforce_limits_for_request()` persists a `reserved` usage row
and commits its limit deltas before returning. The subscription-backed stream,
collect, compact, and transcription paths then calculate upstream rate-limit
response headers before their existing stream or `try`/`finally` settlement
owner is installed. An exception from that header calculation therefore
escapes with no component responsible for releasing the committed reservation.

Source-routed Responses and transcription requests, image requests, and chat
completions calculate headers before reservation or use a different ownership
sequence, so they do not share this gap.

The accompanying HTTP bridge regression work exposed two separate lifecycle
gaps. Failed registration and scheduled/direct cleanup could bypass the close
ownership claim while the upstream reader was also retiring the same session,
allowing duplicate close and resource release. Local terminal reset and
shutdown could remove a session from local reuse before later awaited work;
cancellation or failure at that point could leave no cleanup fallback.

## Goals / Non-Goals

**Goals:**

- Keep cleanup ownership from reservation commit through rate-limit header
  preparation for stream, collect, compact, and transcription requests.
- Release each owned reservation exactly once when header preparation fails,
  then re-raise the original failure without starting upstream work.
- Prove the behavior at the real HTTP route and persistence seam for all four
  request shapes.
- Preserve successful header values and all existing downstream settlement
  ownership.
- Make competing bridge cleanup paths converge on exactly one session close
  owner before post-detachment work can yield.
- Preserve tracked, bounded bridge cleanup through terminal-reset failure or
  cancellation and through shutdown cancellation or individual close failure.
- Prove the lifecycle interleavings deterministically without timing-dependent
  sleeps or external services.

**Non-Goals:**

- Making rate-limit header failures best-effort or returning a fallback header
  set.
- Changing rate-limit queries, caching, serialization, or response schemas.
- Adding another retry or detached-settlement mechanism; persistence failure
  during release remains governed by its existing recovery contracts.
- Changing source, image, chat, WebSocket, or borrowed/forwarded reservation
  ownership.
- Changing bridge routing, durable-owner selection, retry policy, or external
  error envelopes.
- Claiming to resolve the earlier CI-only `bridge_instance_mismatch` failure;
  it was not reproduced or causally linked to these lifecycle defects.

## Decisions

### Centralize the narrow ownership handoff

Add one route helper that calculates rate-limit headers while the caller still
owns the reservation. If calculation exits unsuccessfully, the helper releases
an owned reservation and re-raises. Once headers return successfully, the
existing route-specific stream or `try`/`finally` logic retains responsibility.

Duplicating a `try`/`except` block at each call site was rejected because the
four paths share the same transition and a later route could easily omit one
half of the invariant. Expanding each route's downstream finalizer around all
setup was rejected because streaming paths deliberately transfer reservation
ownership and must not release it when returning a live stream.

### Keep header calculation after reservation admission

The calculation stays after `enforce_limits_for_request()`. Moving it before
admission would avoid the leak, but could return quota metadata that does not
reflect the request's newly committed reservation and would silently change
successful response semantics.

### Exercise the real commit and release path once per transport shape

Use one parameterized ASGI regression covering streaming Responses, collected
Responses, compact Responses, and audio transcription. Each case creates a
limited API key, injects a header-calculation exception after real reservation
admission, wraps the production release helper, and asserts one release call,
one released reservation row, and restored limit usage.

A helper-only unit test as the sole proof was rejected because it would not
prove that every route calls the helper after reservation commit and before
upstream work.

### Claim bridge close ownership before cleanup can yield

Use the existing per-session close-attempt flag as one shared ownership claim.
Failed registration, scheduled cleanup, upstream-reader retirement, local
terminal reset, and shutdown must claim before initiating close; a losing path
must not close the session again. Reader retirement claims before scheduling
its tracked task so an already-detached session still has a cleanup owner and a
concurrent direct path cannot duplicate close.

Keeping separate booleans or path-specific guards was rejected because they
would not serialize cleanup across the reader, reset, registration, and
shutdown entry points.

### Retain a bounded close fallback after detachment

Local terminal reset claims ownership while removing the session under the
registry lock. Pending-request failure remains outside that lock, and a
`finally` path starts bounded close and defers caller cancellation until close
has completed or transferred to tracked background cleanup.

Reader retirement similarly schedules one tracked retirement task and defers
caller cancellation while it detaches and starts any owned close. This keeps
already-detached work visible to shutdown draining without holding the global
registry lock across blocking cleanup.

### Coordinate shutdown before its first await

Shutdown snapshots and clears the local registries under their lock, then
claims all unowned sessions before any waiter notification or other await. It
closes those sessions sequentially through the bounded helper so one close
failure cannot cancel sibling cleanup, drains all tracked bridge-close tasks in
a `finally` path, and re-raises caller cancellation only after the owned cleanup
task completes.

Unbounded parallel close was rejected because it would increase cleanup fan-out
at process exit and make per-session failure ownership harder to reason about.

### Exercise deterministic lifecycle interleavings

Unit regressions use events and explicit task ordering to place reader
retirement against failed registration or scheduled/direct cleanup, interrupt
terminal reset during pending-request cleanup, and cancel shutdown during the
first close. They require exactly one close per session, all claimed shutdown
sessions processed, and no tracked cleanup task left behind.

## Risks / Trade-offs

- **A route bypasses the helper later** → Keep all four cases in one
  parameterized route-level regression.
- **Cleanup is accidentally duplicated by a downstream owner** → Inject the
  failure before downstream construction and require exactly one release call.
- **Release persistence fails independently** → Log that cleanup failure,
  preserve the original header failure, and rely on existing stale recovery
  rather than broadening this fix into a second settlement-retry mechanism.
- **Two bridge paths close one session** → Require every close entry point to
  share one ownership claim and assert one close under forced interleavings.
- **Cancellation arrives after bridge detachment** → Install tracked bounded
  cleanup before propagating cancellation and drain it during shutdown.
- **One shutdown close fails** → Keep close sequencing failure-isolated and
  continue through every already-claimed session.

## Migration Plan

This is a code-only ownership repair with no migration or setting. Deploy it
through the normal release train; rollback is a code revert.

## Open Questions

None.
