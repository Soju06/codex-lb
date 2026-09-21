## Context

See [context.md](context.md) for production observations and reproduction limits. The current implementation already has exact-credit checks and cross-replica serialization. The fix builds on those safeguards while separating credit discovery, deadline execution, request identity, and observed outcome.

## Goals / Non-Goals

**Goals:**

- Attempt opted-in redemption before the selected credit expires, despite a large account inventory or unrelated cache invalidation.
- Recover transient failures without consuming a replacement credit or reporting a request pin as success.
- Keep unrelated Reset buttons and dashboard rows stable after one redemption.
- Make automatic outcomes auditable by account, credit id, original expiry, request id, and actual result.

**Non-Goals:**

- Automatically enable redemption, reset every account, change user-managed `burn_first` policies, or guarantee success during upstream outages.
- Replace the routing system, add an external queue, or change proxy API authentication.
- Deploy, commit, publish a PR, or change production configuration without a separate operator request.

## Decisions

### 1. Persist outcome independently of the existing request pin

Extend `reset_credit_redeem_requests` additively with the selected expiry, origin (`manual`/`automatic`/legacy), outcome, attempt count, next retry time, updated time, upstream code, reset-window count, and redeemed timestamp. Reuse the existing account/request key and immutable credit pin. Historical rows start as `unknown`, never as successful.

Use a small explicit outcome set: `pending`, `retryable`, `unknown`, `confirmed_reset`, `no_reset`, and `expired`. Keep the upstream result fields alongside the classification. A matching redeemed credit with `code=reset` and positive `windows_reset`, or an already persisted equivalent receipt, confirms a reset. An explicit zero-reset result is recorded as such; an unrecognized response or lost response is unknown. Missing credit alone does not resolve an unknown result.

Persist the pin before POST as today. Persist the confirmed result before usage-refresh work. Automatic consumes hand usage verification to a separate worker so slow quota refresh cannot block other expiring credits. A usage-refresh error therefore cannot make a completed consume look retryable. Audit both manual and automatic paths at this common boundary; include outcome, source, account/credit/request ids, expiry and timing, upstream result, and quota-verification status. Do not include credentials or raw upstream payloads. Deduplicate terminal-success audit entries by request id while preserving retry diagnostics.

Automatic request ids become deterministic per account, credit id, and exact expiry, rather than per UTC date. Before starting a new automatic request, reconcile matching historical pins by credit id so a rolling upgrade does not create a second logical request for an already attempted credit. Confirmed requests short-circuit upstream consumption; ambiguous or retryable requests keep their existing id. Automatic retries require a fresh read showing that exact credit and expiry still available, current account eligibility, and time remaining. Backoff is bounded by the credit deadline. After expiry, reconciliation can read state but must not select or consume a replacement. Existing manual/v1 idempotent replay semantics remain supported.

The alternative of deleting a failed pin would allow the next credit to be selected and is rejected. Treating every pin as successful prevents recovery and is also rejected.

### 2. Separate bulk discovery from known deadline execution

Keep per-replica discovery because the snapshot caches remain local. Replace the serial scan with a bounded pool: eight workers total: three for discovery, four for deadline execution, and one for post-consume quota verification. Each worker owns its database sessions; no `AsyncSession` is shared between tasks. Deduplicate concurrent discovery for an account, limit the queue to current eligible accounts, and cancel/drain owned tasks on shutdown or failure.

Maintain an earliest-deadline queue keyed by account, credit id, and expiry. Discovery schedules a wake-up for `expiry - 60 minutes`; credits already inside that window become ready immediately. Deadline wake-ups do not wait for a full scan or its sleep. Reload nonterminal ledger attempts on startup and prioritize previously observed deadlines. Discovery is still needed after a cold start for credits never observed locally; document and measure that limitation.

Keep default discovery interval 60 seconds with existing replica jitter as a scheduling target, and measure actual scan age/queue delay. All workers are included in the eight-worker bound; cap aggregate load across the three production replicas during validation. If throughput cannot meet the acceptance workload, adjust the internal bound based on measurements before shipping, without adding a new operator setting.

Snapshot generation checks continue to prevent stale cache writes. A rejected write does not drop a deadline: enqueue reconciliation, then let the common serialized helper re-fetch the exact target before POST. Re-read the opt-in setting and account eligibility at execution time, including queued work and retries. Disabling the setting cancels future consumes.

Widen the auto window to one hour and update settings copy. Merely increasing the window would leave scan starvation and failed-pin suppression intact; merely increasing concurrency would leave deadline work behind other accounts. Both scheduling separation and margin are needed.

### 3. Use per-account durable revisions for peer invalidation

Add a compact account-revision table, keyed by account id, whose monotonic revision changes whenever a reset-credit snapshot becomes invalid. Reuse the existing shared namespace as a wake-up signal. A new writer increments the account revision and namespace version in one transaction; failed writes retain a bounded repair path and periodic discovery remains the final fallback.

On a notification, read revisions and the namespace version consistently, compare with the replica's baseline, and invalidate only changed accounts. Preserve account-scoped generations so an old in-flight read cannot restore the redeemed credit. Baseline initialization precedes local snapshot population. Comparing revision changes with namespace progress must detect unscoped legacy writes or missing history; those cases can conservatively invalidate the whole store during a mixed-version rollout. Coalesced notifications for multiple accounts must retain all affected account ids. Do not store just one `last_account_id` on a namespace row.

This uses bounded rows proportional to accounts rather than an unbounded event log or dynamic namespace per account. Old readers keep receiving the existing namespace bump. New readers retain a conservative legacy fallback until old writers have drained. Test concurrent/coalesced writes, deletion, restart, and a failed bump explicitly.

### 4. Reconcile one dashboard account after redemption

Add dashboard-authenticated `GET /api/accounts/{account_id}/summary`, returning the existing account-summary shape using targeted repository reads and shared mapping logic. It must not list all accounts or perform upstream I/O. Deleted/missing accounts return the dashboard 404 envelope. Expose a nullable reset-credit freshness timestamp additively so the client can distinguish a temporarily missing snapshot from a fresh zero count. The existing reset-credit listing endpoint remains cache-only.

After consume, fetch that summary plus the account's reset-credit details and trends. Merge the authoritative summary by id into existing account-list and dashboard caches. A reset-credit snapshot that is temporarily absent is pending refresh, not proof that every credit disappeared. Use bounded targeted reconciliation while backend usage/snapshot refresh catches up; do not show a success toast for unknown/no-reset results or optimistically subtract a credit without evidence.

Preserve selected account, filters, pagination, and scroll. A row may move when its actual values change under the selected sort. Recompute totals from updated account data; any needed aggregate-only refresh is coalesced and must not replace the account list. Test both Accounts and dashboard table/grid callers, including partial refresh failures and concurrent redemptions for different accounts.

The alternative of returning a whole account list after consume recreates the reported problem. A targeted summary read also supports delayed usage reconciliation without issuing another consume or changing `/v1` response contracts.

## Risks / Trade-offs

- **Earlier consumption for already opted-in operators** → document the proposed one-hour boundary in settings and change notes; preserve default-off behavior.
- **More simultaneous fetches** → use eight workers per replica, reserving four for deadlines and one for quota verification, measure load and deadline delay using representative inventory and slow failures.
- **Upstream accepted a consume but its response was lost** → retain the pin and unknown outcome; reconcile/retry only the same still-available target. Never infer restoration from absence.
- **Quota data lags a confirmed response** → persist success first and retry only quota/snapshot refresh; expose verification as pending.
- **Mixed-version deployment** → additive migrations, legacy pin reconciliation, existing namespace compatibility, and conservative invalidation fallback.
- **Duplicate irreversible effects on retry** → rely only on the existing same-request/same-credit upstream idempotency contract; exercise it with response-loss fixtures before enabling automatic recovery.

## Migration Plan

1. Implement outcome persistence and safe recovery first. Add migrations on the then-current Alembic head; cover upgrade, downgrade, historical unknown rows, and both databases. Keep the five-minute scheduler until this slice passes route-level regression tests.
2. Implement the deadline scheduler and one-hour setting description after outcome handling is in place. Keep changes within one focused PR; split further if repository size gates require it.
3. Implement account revisions and targeted dashboard reconciliation as a separate focused slice. Retain existing consume response fields and native error envelopes throughout.
4. Validate main specs plus strict change validation, required backend/frontend checks, and before/after dashboard screenshots. When implementation is verified, synchronize the deltas/context and archive the change.
5. Deployment is a later operator action through the HA deploy skill and surge rollout. Observe deadline delay, outcome counts, unknown/retryable backlog, and account-list request frequency. If a rollback is explicitly requested, retain additive data while using the supported HA rollback path; do not drop outcome records during an incident.

## Open Questions

The implementation uses eight total workers per replica and a one-hour window. Synthetic benchmarks exercise one and three replicas with 1,200-account inventories, 100 concurrent five-minute deadlines, and ten seconds total per redemption. Actual production latency and cold-start discovery remain deployment observations. Unknown upstream response variants must be captured as sanitized fixtures and remain unknown until their semantics are verified; they are not assumed to mean success.
