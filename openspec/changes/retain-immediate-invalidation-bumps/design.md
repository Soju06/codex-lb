## Context

See `proposal.md` for the failure and extraction from #2463. `CacheInvalidationPoller` already owns a namespace set and a bounded poll loop. `_flush_pending_bumps()` retains failed coalesced work, while a direct `bump()` returns false after its short retry budget without recording later work. Existing route-cache callers compensate independently.

## Goals / Non-Goals

**Goals:** close the direct-publication gap using the existing queue; preserve one marker per namespace, mutation-before-notification ordering, independent session ownership, callback acknowledgement, and cancellation propagation.

**Non-Goals:** durable delivery across process loss, draining on shutdown, namespace/callback changes, removal of route-cache caller fallbacks or settings' dual publication, API-key usage-share policy, migrations, settings, or new workers.

## Decisions

1. **Move marker consumption into `bump()`.** Consume only work queued before the immediate attempt starts, before any await. A request arriving during that attempt re-adds the marker and is never erased on completion. Leaving marker management split across callers cannot cover direct publication races uniformly.
2. **Restore unfinished work in a synchronous `finally` block.** A success flag is set only after `_bump_once()` returns, including its session cleanup. Failures, cancellation during the write or retry backoff, and ambiguous post-commit interruption retain the marker; cancellation still propagates. The original small lock retry budget and failure metrics remain unchanged.
3. **Keep the flush's defensive exception isolation.** The normal false-return path no longer needs a separate requeue operation, but abnormal exceptions cannot starve later namespaces. A canceled flush restores its current namespace and propagates cancellation.
4. **Keep existing publication topology.** The old extracted patch also removed route-cache fallback glue and one settings namespace bump. Those cleanups are unnecessary for the retry fix and would broaden the operator contract. They are intentionally omitted; the existing set coalesces duplicate fallback requests harmlessly.
5. **Exercise real mutations independently of #2463.** An API-key active-state update and account pause use existing main routes and independent peer caches. Fail the source invalidation writes after the data commit, prove stale peer state before recovery, then prove convergence after the source retry and peer poll.

## Risks / Trade-offs

- Ambiguous commit outcomes can cause a duplicate namespace increment. Existing callbacks are idempotent; duplicating an invalidation is safer than losing it.
- Pending work is process-local. Process exit or a stopped/wedged poller can still lose/delay publication; this is not a durable outbox or a new recovery guarantee for caches without a TTL.
- Failed `bump_local()` publications retry through the generic bump path and may trigger redundant source invalidation. Successful self-acknowledgement remains unchanged.
- Poll and database contention can delay recovery. No unbounded immediate retries, new tasks, or new shared sessions are introduced; retries occur only on existing poll cycles.

## Migration Plan

No schema migration or setup. Roll out through the normal release process; mixed old/new replicas are compatible because the version table and callback topology are unchanged. Rollback restores old best-effort direct-publication behavior. No production rollout is part of this PR.
