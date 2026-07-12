# HTTP Bridge Agent Isolation Context

## Purpose

Codex can run several independent agent threads inside one process session.
Those threads share the session header but carry distinct explicit
`prompt_cache_key` values. The bridge combines both values so a sequential
child cannot inherit its parent's upstream conversation.

## Rollout and failure modes

- During a mixed-version rollout, an unanchored owner forward may fail with
  `bridge_forward_upgrade_required`. Retry after all bridge instances run the
  same protocol version; do not bypass the signed continuity check.
- A durable alias rejected by owner-epoch fencing is removed locally. A later
  409 continuity error means the request reached an instance that can no longer
  prove ownership and should be retried through the current bridge ring.
- Parallel or differently keyed agent threads create additional bridge lanes.
  Higher session churn is expected; capacity remains bounded by the existing
  bridge and account limits.

## Example

A parent and leaf agent may both send `session_id=process-1`, while their
explicit prompt-cache keys are `parent-thread` and `leaf-thread`. They receive
different stable bridge identities. Repeated requests from `leaf-thread` reuse
the leaf identity, including after a generated turn-state alias fallback.
