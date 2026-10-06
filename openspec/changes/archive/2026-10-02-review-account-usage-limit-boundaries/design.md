## Context

See [proposal.md](proposal.md). The branch already shares a pure policy evaluator and uses selection-cache generations to detect stale admission. Bridge readers need the pending lock to settle other turns; send serialization uses a separate lifecycle lock.

## Goals / Non-Goals

Close demonstrated authorization gaps using the current evaluator and cleanup paths. Preserve the scalar policy, upstream health semantics, existing ownership, and disabled-feature behavior. Integrate the latest upstream with a local merge as requested; publishing remains outside this local task.

## Decisions

- Put the final bridge authorization after lifecycle-lock waits and durable preparation, before pending publication and send. Keep early admission checks to avoid queueing known-denied work. Merely adding another pre-lock check does not close the race.
- If selection data changes during sticky persistence, release the lease and return the existing retryable `selection_state_changed` error. Avoid speculative compensating affinity writes that can overwrite another request's ownership.
- Represent an empty successful poll using existing no-data placeholders in all standard slots for enabled policies. This supersedes historical weekly and monthly shapes without adding columns. Partial valid shapes continue using the existing normalization rules.
- Retain the shared evaluator and existing cache rather than introduce policy-specific services or more settings. Simplify redundant typed access and work only where verified behavior stays identical.
- Merge upstream without rebasing existing branch history. Add an Alembic merge revision for parallel usage-limit and upstream heads, preserving both upgrade paths and existing account policy data.
- Adapt policy authorization to upstream's fixed refresh cadence, injected clock/scheduler, dashboard account-write permission, usable-access-token handling for reauthentication warnings, and fenced warmup claims. Preserve upstream's settings snapshots, overload isolation, denied-anchor fences, and task ownership.
- Move usage-snapshot authorization into a typed internal balancer module to keep the main balancer within the existing size ratchet. Reuse upstream's ORM snapshot copier; remove the duplicate empty-policy-pool selector call and the runtime-account lookup memo.

## Risks / Trade-offs

- A slow authorization read holds send serialization longer; it remains deadline-bounded and does not hold the reader's pending lock.
- An empty successful poll now blocks an enabled policy immediately. Fresh valid telemetry or disabling the policy restores eligibility.
- Affinity may already be committed when a late policy invalidation is noticed. Returning a retryable error preserves that ownership and avoids unsafe rollback.
- Upstream reporting and already-dispatched requests can still overshoot a cap; the guarantee remains observation-bound.
