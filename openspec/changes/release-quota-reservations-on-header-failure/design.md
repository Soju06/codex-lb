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

**Non-Goals:**

- Making rate-limit header failures best-effort or returning a fallback header
  set.
- Changing rate-limit queries, caching, serialization, or response schemas.
- Adding another retry or detached-settlement mechanism; persistence failure
  during release remains governed by its existing recovery contracts.
- Changing source, image, chat, WebSocket, or borrowed/forwarded reservation
  ownership.

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

## Risks / Trade-offs

- **A route bypasses the helper later** → Keep all four cases in one
  parameterized route-level regression.
- **Cleanup is accidentally duplicated by a downstream owner** → Inject the
  failure before downstream construction and require exactly one release call.
- **Release persistence fails independently** → Log that cleanup failure,
  preserve the original header failure, and rely on existing stale recovery
  rather than broadening this fix into a second settlement-retry mechanism.

## Migration Plan

This is a code-only ownership repair with no migration or setting. Deploy it
through the normal release train; rollback is a code revert.

## Open Questions

None.
