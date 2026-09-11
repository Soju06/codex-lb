## Context

See `proposal.md` for motivation. The usage updater mutates and synchronizes the
selected account only after its existing paid-to-Free confirmation policy is
satisfied. The warm-up service currently sees only the post-refresh account and
requires matching canonical before/after windows, so it cannot distinguish a
confirmed plan transition from an account that was already Free.

The existing `usage_reset_confirmed` guard protects ordinary reset detection
from cross-window comparisons and timestamp drift. The transition path must not
weaken that guard.

## Goals / Non-Goals

**Goals:**

- Carry enough refresh-scoped evidence to identify a confirmed paid-to-Free
  transition without introducing new persistent state.
- Require the monthly candidate to have been written by the same refresh and
  to pass the existing availability, account, and global opt-in gates.
- Reuse the existing monthly warm-up identity and atomic claim.

**Non-Goals:**

- Changing paid-to-Free confirmation or ordinary same-window reset detection.
- Adding settings, schema, migrations, retry queues, or periodic backfill.
- Sending warm-up traffic to inactive or non-opted-in accounts.

## Decisions

### Snapshot the selected account plan before refresh

The scheduler will preserve the selected account's normalized pre-refresh plan
and pass it to warm-up evaluation after reloading the account. A transition is
eligible only when the snapshot is a recognized paid plan and the persisted
post-refresh plan is `free`.

Alternative considered: infer a transition from `secondary` to `monthly` usage
rows. That would incorrectly classify already-Free accounts whose first monthly
sample arrives after stale secondary history.

### Require a monthly sample written during the same refresh

The fallback candidate will accept only the selected long-window row when its
canonical window is `monthly`, it has a reset deadline, and its `recorded_at` is
at or after the refresh start. It will apply the existing minimum-availability
gate before returning a candidate.

Alternative considered: use the latest persisted monthly row regardless of
age. That could warm stale quota after an unrelated plan metadata update.

### Keep the transition as a fallback to normal reset detection

The service will first evaluate the existing same-window reset candidate. Only
when that returns no candidate for the configured long window will it evaluate
the paid-to-Free transition. The resulting candidate uses `window="monthly"`
and the monthly `reset_at`, so the existing atomic attempt claim provides
deduplication.

Alternative considered: alter `usage_reset_confirmed` to allow cross-window
transitions. That would weaken a safety guard used by status recovery and
ordinary warm-up paths.

## Risks / Trade-offs

- [A process exits after persisting plan and usage but before warm-up] → The
  transition can be missed, matching the current event-triggered reset path;
  avoid new persistence until stronger delivery semantics are required.
- [A future updater mutates plan before confirmation] → Keep regression coverage
  at scheduler/service boundaries and rely on the updater's existing durable
  two-observation confirmation contract.

## Migration Plan

No data migration is required. Deploy the code normally; rollback restores the
previous behavior without changing stored warm-up attempts or usage history.
