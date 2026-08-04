## Context

`CacheInvalidationPoller.prime()` normally records every namespace version before startup warms process-local caches. If that read fails, startup logs the failure, warms the routing and model-registry state, and starts background polling with no known versions. The first successful poll currently treats every observed row as a callback-less baseline, even if a peer advanced it after the failed read.

The poller therefore has two distinct lifecycle phases: explicit baseline acquisition before process-local state is served, and background reconciliation after that state may be warm. Only the former may accept an observed version without invoking its callbacks.

## Goals / Non-Goals

**Goals:**

- Recover conservatively after a failed startup baseline read.
- Run registered callbacks before acknowledging positive versions first observed during background polling.
- Preserve callback retry and monotonic version-acknowledgement behavior.
- Preserve baseline-only behavior when `prime()` is explicitly retried before background polling.
- Prove the fix at both the warmed upstream-route resolver cache and the account-routing / bridge-session reuse seam.

**Non-Goals:**

- Failing startup or adding startup retries.
- Changing namespace versions, bump ordering, callback registration, poll intervals, or cache TTLs.
- Adding configuration, schema, migrations, or cross-replica payloads.
- Changing normal startup behavior when baseline priming succeeds.

## Decisions

### Background start ends callback-less baseline acquisition

`start()` will transition an uninitialized poller into conservative background-reconciliation mode before it creates the polling task. A successful prime has already made this transition with recorded versions. After a failed prime, the transition makes a positive version with no known baseline satisfy the existing change predicate, so the namespace callback runs before acknowledgement.

This uses the existing `_poll_initialized` state rather than adding a second failure latch. The relevant distinction is not whether one particular read failed, but whether the caller is still explicitly acquiring a pre-service baseline or has started background polling after caches may be warm.

Alternatives considered:

- Mark the poller initialized inside the `prime()` failure path: rejected because an explicit `prime()` retry could then invoke callbacks even though its documented purpose is baseline-only acquisition.
- Retry or fail startup: rejected because it changes availability policy and is broader than the stale-cache defect.
- Clear routing caches directly in `app.main` after the exception: rejected because it duplicates callback wiring, does not cover later first-observed namespace rows, and leaves other poller consumers inconsistent.
- Add a separate recovery latch: workable, but redundant with the existing lifecycle boundary and adds state combinations without improving the guarantee.

### Recovery uses normal callback acknowledgement rules

Recovery will use `_run_callbacks()` and the existing per-namespace acknowledgement path. A callback failure leaves that namespace unacknowledged and the next poll retries it. Existing monotonic handling for concurrent `bump_local()` acknowledgements remains unchanged.

### The integration proofs keep the real routing gates

The regressions will use two pollers sharing the integration database. One keeps a resolved upstream-route outcome warm across a failed prime and a peer `upstream_route` bump. The other keeps a routing snapshot seeded `ACTIVE` across a peer status change plus `account_routing` bump and checks the actual bridge-session reuse predicate. Together they prove that recovery changes externally relevant routing decisions, not merely a callback counter.

## Risks / Trade-offs

- [A failed prime followed by background start may replay callbacks for versions that predate startup] → Callbacks are designed to be idempotent local reconciliation; this conservative replay happens only on the exceptional no-baseline path and is safer than serving unknown cache state.
- [A recovery callback can fail] → The existing unacknowledged-version retry path remains authoritative and is covered by focused tests.
- [The background task could poll before recovery mode is active] → Transition lifecycle state synchronously in `start()` before creating the task.
- [A namespace has no row yet] → No callback runs until a later bump creates a positive version; that first observed version is then treated as changed.

## Migration Plan

No data or configuration migration is required. Deploy as an application-only change. Rollback restores the previous callback-less first-poll fallback after a failed prime; no persisted state requires reversal.

## Open Questions

None.
