## Context

See proposal.md. Main now owns SCIM/overflow convergence; #1528 has its own pending migration chain. Allocation correctness still requires policy eviction when account capacity or routing eligibility changes.

## Goals / Non-Goals

**Goals:** one Alembic head on current main, one scoped feature, bounded repeated HTTP authentication work, and unchanged streamed failure refresh behavior.

**Non-Goals:** approving a new limit policy, selecting fail-closed semantics, rebasing #1528, or releasing the independent invalidation reliability change.

## Decisions

- Parent `20260930_000000_add_api_key_usage_share_percent` on main's convergence revision; remove only this branch's unmerged alternate convergence. Upgrade coverage begins from both landed convergence parents. Recheck the intended parent after #1528 lands.
- Restore generic cache-invalidation and upstream-route code, tests, and specs from main. Preserve the removed change as a local patch for a separately authorized PR, not as an implicit feature dependency.
- Account mutations explicitly publish the existing `api_key` namespace as well as routing. Do not clear API-key caches merely because an observed routing version changes. Preserve local synchronous eviction and the API-key cache's version fence; queue a retry with the existing primitive if an awaited API-key bump fails.
- Cache incomplete snapshots for at most five seconds, never the ordinary sixty seconds. Complete estimates retain their evidence/reset/routing expiry. The existing API-key namespace still invalidates all snapshots on mutations. Admission retains one debounced best-effort refresh request; this does not settle the policy approval question.
- Keep main's refresh helper behavior in the common helper used by both streamed usage-limit rejection and incomplete-evidence admission.

## Risks / Trade-offs

- Incomplete snapshots can remain fail-open for five seconds after new evidence arrives; this explicit short bound avoids per-request pool scans without a second cache or allocator.
- Concurrent cold authentication misses are not serialized by this change; repeated cache hits are bounded, not a new singleflight mechanism.
- Unpublished alternate migration stamps are not treated as released compatibility contracts. Existing landed SCIM and retirement stamps remain supported.
- #1528 may land first; migration topology must be rechecked before publishing/merging.

## Example

Ten requests with a newly added account and missing quota evidence reuse one incomplete snapshot within five seconds. The first request after the short expiry reads fresh evidence; ordinary complete snapshots retain their existing sixty-second TTL and earlier evidence boundaries.
