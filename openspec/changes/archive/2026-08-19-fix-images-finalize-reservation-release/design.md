## Context

Image generation and edit reserve limited API-key quota before invoking the
internal Responses pipeline. They intentionally pass no reservation into that
pipeline because image usage comes from `tool_usage.image_gen`, not
`response.usage`. The image adapter therefore owns final settlement.

The current adapter performs finalization inline after it has already produced
the public image result. A persistence failure rolls the reservation back to
`reserved`; the adapter logs and returns, so no request-scoped owner remains.
The standard Responses settlement path already provides detached task tracking,
cancellation handoff, retrying release, bounded repository concurrency, and
graceful persistence drain.

## Goals / Non-Goals

**Goals**

- Transfer image reservation ownership exactly once to the existing tracked
  settlement machinery.
- Finalize captured image tokens when persistence succeeds.
- Preserve the completed public response while failed or cancelled settlement
  transfers ownership to the existing retrying release fallback.
- Keep generation/edit and streaming/non-streaming behavior aligned.

**Non-Goals**

- Define image-only retries of authoritative token finalization.
- Change repository states, retry timings, concurrency limits, stale-reset
  policy, database schema, settings, or external response shapes.
- Give the internal Responses stream a second settlement owner.
- Broaden pre-terminal image cancellation cleanup.

## Decisions

### Reuse tracked stream settlement ownership

Add one image-facing adapter on the API-key usage mixin. The adapter constructs
the existing settlement value from the public image model, captured image
tokens, API-key data, reservation, service tier `None`, and request id, then
delegates to the existing tracked settlement entrypoint.

This keeps task registration, cancellation callbacks, retrying release, and
persistence drain in one implementation rather than copying lifecycle logic
into the route module.

### Preserve image-token authority

When at least one captured image token field is usable, the adapter records a
successful settlement and normalizes missing token fields to zero. When no
captured image usage is usable, it selects the existing non-success settlement
path so the reservation releases instead of recording fabricated usage.

The internal Responses call continues receiving `api_key_reservation=None`.

### Transfer ownership before returning the completed result

All four image completion paths call the same adapter exactly once. The adapter
returns after the settlement task is registered; the public response does not
wait for persistence. If tracked finalization fails or is cancelled, its done
callback transfers ownership synchronously to the retrying release task.

Exactly-once refers to the terminal database mutation. Retried release attempts
remain safe because repository transitions claim only a still-reserved row.

## Risks / Trade-offs

- A failed finalization falls back to release, so successful image usage can be
  omitted under persistence failure. This matches existing standard stream
  policy and is preferable to keeping quota ownerless. Retrying authoritative
  finalization is a broader accounting-policy change and remains separate.
- Reusing a private settlement value couples the adapter to existing settlement
  internals. Keeping construction inside the mixin limits that coupling and
  avoids route-level task lifecycle duplication.
- Permanent release failure leaves quota conservatively reserved, but the task
  remains visible to persistence drain and the stale reaper remains a final
  process-restart fallback.

## Migration Plan

No migration or rollout setting is required. Existing terminal reservations are
unchanged; new image completions use tracked settlement after deployment.

Rollback restores inline image finalization behavior without data conversion.

## Open Questions

None for this change. Stronger retries of authoritative image finalization are
explicit follow-up scope.
