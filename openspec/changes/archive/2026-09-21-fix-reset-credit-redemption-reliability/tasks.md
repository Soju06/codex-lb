## 1. Outcome safety and same-credit recovery

- [x] 1.1 Turn the failed-pin, lost-response, HTTP-200/no-reset, and consume-success/usage-refresh-failure cases into regression tests at the dashboard consume route and shared automatic path.
- [x] 1.2 Add outcome/expiry/origin/retry fields to the redemption ledger on the current Alembic head; backfill historical pins as unknown and test PostgreSQL/SQLite upgrade, downgrade, and single-head graph.
- [x] 1.3 Classify and persist upstream outcomes before refreshing usage; stop unconditional count decrements and preserve existing consume response fields and error envelopes.
- [x] 1.4 Implement bounded same-request/same-credit recovery with fresh eligibility, setting, expiry, and availability checks; resolve legacy pins before generating per-credit automatic request ids.
- [x] 1.5 Emit sanitized manual/automatic outcome audit records and retry diagnostics; keep usage-verification failures separate and terminal-success audit entries idempotent.
- [x] 1.6 Add multi-replica response-loss/replay tests and PostgreSQL advisory-lock and SQLite claim tests, including lease renewal, partial failure, and repeated cancellation cleanup.
- [x] 1.7 Verify canonical dashboard/v1/proxy consume surfaces, trailing slash behavior, authentication, required-capability rejection, no-body requests, and existing native response/error contracts.

## 2. Deadline scheduling and one-hour window

- [x] 2.1 Add fake-clock regressions for 1,200 eligible accounts, a credit entering the window between visits, cache-generation rejection, and a deadline queued behind slow discovery work.
- [x] 2.2 Replace sequential discovery with a bounded pool, eight workers per replica: three discovery, four deadline, one quota-verification; give every task its own database sessions and test the concurrency ceiling.
- [x] 2.3 Add the exact-credit deadline queue and startup reconciliation for persisted unresolved attempts; prevent duplicate per-account discovery and bound queue growth.
- [x] 2.4 Schedule opted-in redemption at the one-hour boundary, recheck the setting/account immediately before consume, and preserve exact credit id plus expiry on every retry.
- [x] 2.5 Test disabling auto-redeem, pause/delete/reauth transitions, changed expiry, disappeared target with a later credit still available, expired retries, restart, and shutdown task cleanup.
- [x] 2.6 Benchmark three replicas with representative inventory and simulated slow/time-out responses; prove known deadline work bypasses the discovery backlog and record scan duration, deadline completion/queue delay, and aggregate request load. Set worker sizing from those results before shipping.
- [x] 2.7 Update reset-credit settings descriptions and translations to the one-hour window while retaining the existing boolean setting and default-off behavior.

## 3. Account-scoped cache invalidation

- [x] 3.1 Add durable per-account snapshot revisions and compatible migration coverage; write revisions and namespace progress coherently and define bounded repair for failed writes.
- [x] 3.2 Reconcile changed revisions on each replica and invalidate affected accounts only; preserve per-account generation protection and initialize baselines before snapshot population.
- [x] 3.3 Test two coalesced account changes, self-notifications, unchanged peers, old in-flight fetches, account deletion, restart, lost writes, and mixed-version unscoped invalidation fallback.
- [x] 3.4 Add integration coverage showing redemption of X preserves Y's count/expiry on every updated replica, while X's redeemed credit disappears within the normal poll bound.

## 4. Targeted dashboard updates

- [x] 4.1 Add nullable reset-credit fetch freshness to account summaries and a targeted authenticated account-summary endpoint using existing mapping logic without full account enumeration or upstream I/O.
- [x] 4.2 Test summary response shape, local-only reads, deleted/missing accounts, authentication, dashboard error envelopes, and canonical/trailing-slash behavior.
- [x] 4.3 Replace broad post-redeem query invalidations with per-account summary/detail/trend reconciliation and cache merging by account id; coalesce any aggregate-only refresh.
- [x] 4.4 Show confirmed reset, no-reset, and pending/unknown outcomes truthfully; handle missing snapshots and delayed quota refresh with bounded targeted polling, never another consume.
- [x] 4.5 Add UI regression coverage for Accounts and dashboard table/grid: no mutation-triggered full-list request, unrelated buttons preserved, concurrent target updates, partial refresh errors, and selection/scroll preservation and unchanged list-query identity (filter/pagination state is not rewritten).
- [x] 4.6 Capture before/after dashboard screenshots and verify totals plus the existing reset-credit sort using authoritative updated values.

## 5. Verification and delivery

- [x] 5.1 Run focused backend integration/unit/migration checks and frontend checks required by each implementation slice; run lint/type checks appropriate to changed modules.
- [x] 5.2 Run strict validation for this change and main specs; verify every delta scenario against implementation evidence, including outcome recovery and both external dashboard surfaces.
- [x] 5.3 After implementation verification, synchronize delta specs and stable context into the owning main capabilities and archive the completed change.

## Later operator actions (outside this local implementation)

- If PR publication or merging is requested, split the staged implementation into focused outcome, scheduler, and scoped-cache/dashboard slices as needed for repository size gates, then check the actual GitHub readiness gates. No PR, commit, or push was requested or performed.
- If deployment is requested, use the HA deployment skill and surge rollout; observe readiness, deadline delays, unresolved outcomes, unrelated counts, and dashboard requests under real upstream latency. No deployment or production auto-redeem configuration change was performed.
