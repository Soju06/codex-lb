# Context — replica-safe-reset-credits

## Purpose

Reset credits are scarce, paid, human-redeemed resources. Every defect fixed
here burns one (double redeem) or blocks a legitimate redemption (false 409),
and all of them only manifest with more than one process sharing a database —
exactly the deployment shape `docs`-level replicas and multi-process SQLite
setups use.

## Failure modes and how they are covered

| Failure (before) | Mechanism (after) | Bound |
| --- | --- | --- |
| Retry on replica B consumes a second credit | durable `(account_id, redeem_request_id) -> credit_id` ledger committed before the consume call | exact (any replica) |
| Two SQLite processes redeem concurrently | atomic claim-row upsert with 30s lease renewed every 10s by a holder heartbeat | exact while claimant lives (including slow redemptions); 30s lease on crash |
| v1 false 409 from a fresh replica | authoritative upstream fetch on snapshot miss | one extra upstream round-trip, miss path only |
| Peers list a redeemed credit for <=60s | `reset_credits` bus namespace, full-store clear | ~0.5s poll bound; refresh tick is the fallback |

## Tradeoffs accepted

- **Full-store clear on the bus**: the version-counter bus carries no payload,
  so a peer redeem clears every account's snapshot on every replica. Peer
  dashboards briefly show `available_reset_credits: 0` until the next refresh
  tick repopulates (<=60s, usually much less). Bounded, and only triggered by
  rare redeems. The alternative — per-account namespaces — leaks unbounded
  rows into `cache_invalidation`, which every replica scans twice per second.
- **Failed-consume pins are kept**: a same-`redeem_request_id` retry retargets
  the same (possibly upstream-expired) credit rather than silently picking
  another. Deliberate: the caller asked to redeem *that* credit once; a new
  user action generates a new `redeem_request_id`. Pins expire after 24h.
- **Wall-clock lease on SQLite claims**: multi-host clock skew could allow
  early takeover, but SQLite multi-process deployments are effectively
  single-host/volume, and the holder heartbeat (renew every 10s against a 30s
  lease) keeps the claim alive even when a redemption legitimately outlives
  one lease (usage-fetch retries plus the upstream consume can exceed 30s).
  Expiry-based takeover only happens once renewals stop (crash/hang).
- **Claim retry latency**: up to 15s under contention before the 409 —
  acceptable for a human-driven dashboard action; PostgreSQL deployments keep
  the blocking advisory lock instead.
- **Delayed first refresh**: the startup jitter delays a fresh replica's first
  snapshot fetch by up to one interval. The v1 authoritative fallback covers
  redemption during that window; dashboards show zero credits until the first
  tick, as they already did on restart.

## Implementation notes

- **Poller lifecycle is symmetric**: lifespan shutdown clears the
  process-global cache-invalidation poller, so `bump_cache_invalidation` is a
  no-op outside the poller's lifetime (before startup and after shutdown)
  rather than writing through a stopped poller. This is what keeps the bump
  best-effort at the edges of the process lifecycle.

## Example: cross-replica retry

1. Operator clicks redeem; the dashboard sends `redeem_request_id=R`.
2. Replica A pins `(acct, R) -> credit-1` in `reset_credit_redeem_requests`,
   commits, forwards the consume — and the LB kills the connection before the
   response arrives.
3. The dashboard retries with the same `R`; the LB routes it to replica B.
4. Replica B acquires the per-account claim, reads the pin, and forwards
   `credit-1` again. Upstream answers `already_redeemed` idempotently.
   Credit-2 stays unburned. (Before this change, B had no memory of `R` and
   consumed credit-2.)

## Follow-up (out of scope)

Moving snapshots into a shared DB table would eliminate the per-replica
upstream fetch amplification and the full-store-clear tradeoff at once, at the
cost of a larger schema/read-path change. Tracked as a candidate follow-up
change.
