# Reliable reset-credit redemption

The [requirements](spec.md) separate credit discovery, expiry work, and confirmed outcomes. A pin records the exact selected credit before an upstream POST; it does not prove that quota was restored. Historical pins therefore migrate to `unknown`.

## Timing and capacity

When the existing opt-in toggle is enabled, the automatic window starts one hour before expiry. Each replica reserves three workers for discovery, four for due redemptions, and one for quota verification. Known deadlines bypass the full account scan. Discovery keeps startup jitter; persisted unresolved attempts resume immediately when the scheduler starts. Eligible retries keep their original expiry priority.

Synthetic tests cover 1,200-account inventories and 100 credits each with five minutes remaining. With ten seconds total per successful redemption (pre-consume fetch plus consume), one replica finishes the burst in approximately 255 seconds and three simulated replicas in approximately 260 seconds, while quota verification stalls. The three-replica discovery scan takes approximately 433 simulated seconds and makes 3,600 fetch attempts. These are workload assumptions, not production latency measurements. Cold starts still need discovery for credits never observed or persisted; upstream outages and longer latency can exceed the available margin.

## Recovery example

C1 expires at 15:00 and C2 next week. C1 becomes due at 14:00. If its response is lost, the ledger retains C1 and its request id. A retry requires C1 to remain available with its original expiry. C1 disappearing does not authorize substituting C2. A matching redeemed receipt with a positive reset-window count confirms restoration; a zero-window result or unknown response remains distinct. After confirmation, retries refresh quota without another consume. Routing policy, including `burn_first`, is not changed merely because a credit disappears.

## Cache and rollout

Per-account revisions and namespace progress are committed together. Updated replicas invalidate changed accounts only, preserving unrelated buttons. Legacy unscoped writes or lost revision history conservatively clear the cache during mixed-version operation. Writes have bounded retries and the existing periodic scan is the eventual repair path. The targeted account-summary endpoint checks the account revision and uses local database/cache reads only.

The additive outcome/revision migrations support SQLite and PostgreSQL. Deploy through the existing HA surge workflow only after an operator requests deployment. Observe discovery duration, due-work backlog, unknown/retryable outcomes, and confirmed receipts still awaiting usage verification. No environment setting or external queue is introduced.

## Review follow-up

Scheduler verification receives detached account data before background-session rollback, including each row published during partially failed restoration; the real consume/session regression processes eight accounts and confirms the worker group remains alive. This lifecycle coverage complements the synthetic queue-capacity benchmark, which mocks automatic redemption and cannot establish production throughput.

Received consume results are settled in an owned task that defers cancellation, retries persistence up to three times in fresh sessions, and invalidates the affected snapshot. Confirmed unverified receipts remain discoverable after cancellation/restart. A prolonged database outage or process death can still prevent durable settlement; the system does not fabricate confirmation or re-consume merely to repair a failed write.

With twenty seconds remaining, a fast transient failure schedules the same request approximately ten seconds later, reserving half the lifetime for the next attempt. Retry delays stop shrinking once only two seconds remain; the worker then waits until expiry and drops the work. Future persisted retry timing is respected after restart. If a preflight fails after the stored retry became due, the scheduler computes a new future delay instead of looping on that past timestamp.

Manual requests with different IDs cannot bypass an existing selected-credit pin, even when upstream's available-items list is stale. New client IDs for an unresolved manual credit are durably pinned to that credit, then resolve to the original request for upstream attempts and receipts. Thus a browser reload can recover without issuing a new upstream identity; subsequent retries of either ID remain bound to C1 even when only C2 is available. Legacy no-body callers also recover the selected credit's existing unresolved request. Reconciliation caps availability at the authoritative count, and targeted account summaries preserve duplicate-identity classification without enumerating the full list.

Alias pins use the existing origin field (`alias`), and canonical selection prioritizes non-alias pins before timestamp ordering. Read and purge eligibility are defined for the account/credit group: any pin inside the 24-hour window retains that group's original request and outcome. This prevents clock skew or an older canonical timestamp from causing a second upstream identity while a valid alias still refers to it. When every group member ages out, ordinary opportunistic purge removes the group.
