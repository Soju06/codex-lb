## Context

Persisted `status`, `reset_at`, and `blocked_at` are the cross-replica authority for account availability. A 429 therefore survives process restarts and peer selection, but the current recovery gate also treats every future `reset_at` as authoritative even when usage history proves that the exact monthly window associated with the block has reset. Free accounts can consequently remain `rate_limited` through a fresh monthly window, and the warm-up layer correctly refuses to contact them because they are not `active`.

The scheduler already captures usage immediately before and after a selected-account refresh, usage history survives restarts, account status writes support compare-and-set guards, and warm-up attempts are deduplicated by account/window/reset. The design uses those existing primitives and does not introduce a setting or a second recovery mechanism.

## Goals / Non-Goals

**Goals:**

- Recover a Free account from a stale future rate-limit marker when a temporal monthly transition proves that the blocked window reset and fresh quota is available.
- Preserve the 30-second post-429 floor and the full persisted cooldown for generic 429, Retry-After, jitter-only, stale, mismatched, or exhausted evidence.
- Make recovery atomic and order it before ordinary active-only warm-up.
- Preserve recovery and warm-up behavior across scheduler/process restarts by using persisted usage history.
- Reuse one confirmed reset tuple for status recovery and durable warm-up deduplication.

**Non-Goals:**

- Probe or warm a still-blocked account as a way to infer whether a throttle ended.
- Generalize early recovery to Plus/Pro primary-window exhaustion, `quota_exceeded`, auth failures, paused/deactivated accounts, model-scoped throttles, or generic Retry-After cooldowns.
- Add configuration, schema, migration, dashboard, API, or new background-worker behavior.
- Change the existing recovery rules after a persisted cooldown has naturally elapsed.

## Decisions

### Resolve one canonical monthly reset-evidence tuple

For the selected account, the scheduler will first test its in-memory monthly before/after samples with the existing temporal reset-confirmation predicate. A blocked Free account must still query persisted monthly history recorded since `blocked_at` when that current pair confirms a transition whose baseline does not anchor to the current block marker. That history must contain a baseline recorded strictly after `blocked_at` whose reset deadline matches the account marker; a matching row at the exact block timestamp is not eligible and cannot shadow a later valid baseline. Only adjacent pairs at or after the eligible baseline are then evaluated, and the most recent pair that passes the temporal reset predicate becomes the transition evidence. This handles upstream monthly deadlines that slide between samples without comparing non-neighboring rows, while preventing newer unanchored evidence from masking a valid persisted recovery path.

The persisted lookup makes the evidence restart-safe: a process that starts after the transition can recover from the same history rather than waiting for the stale account deadline. Both paths retain the matching baseline alongside the canonical `(before, after)` transition, and the normal reset predicate remains the single authority for scheduled-boundary crossing or a quota-recovery re-anchor within the observation interval. Warm-up consumes only the adjacent transition pair; recovery additionally checks the matching baseline.

Alternative considered: infer recovery from repeated `monthly < 100%` snapshots. Availability alone does not identify which 429 or quota window ended and would weaken generic Retry-After protection.

### Require the reset evidence to identify the current block

Early recovery applies only to a `rate_limited` Free account with both markers present and a still-future persisted `reset_at`. The scheduler requires all of the following:

- at least 30 seconds have elapsed since `blocked_at`;
- the evidence is a monthly-to-monthly temporal reset transition;
- persisted post-block history contains a monthly baseline whose `reset_at` matches the persisted account `reset_at` within five seconds;
- the reset evidence is an adjacent monthly pair at or after that matching baseline;
- the transition's after sample and the latest monthly sample were recorded after `blocked_at` and both report usage below 100 percent.

The deadline match binds the usage transition to the block being recovered, while the latest-sample check prevents an earlier good sample from reactivating an account that exhausted the new window again. Free-plan scoping ensures a Plus account with an exhausted primary window cannot be released by unrelated long-window availability.

Alternative considered: let any fresh available usage override a future deadline after 30 seconds. That cannot distinguish a quota-window reset from a generic throttle or a model/account restriction.

### Recover through marker-guarded compare-and-set before warm-up

The scheduler will perform recovery before invoking warm-up. The status transition compares the current status, deactivation reason, `reset_at`, and `blocked_at`; on success it writes `active`, clears the reason and both block markers, and updates the selected detached account passed to warm-up. A miss leaves that object blocked, so a concurrent newer 429 or operator change wins and no warm-up candidate is evaluated from the stale snapshot.

Warm-up candidate selection remains restricted to `active`, and the sender independently reloads account state and requires `active` immediately before network I/O. These two checks cover both a failed recovery CAS and a re-block that lands after candidate creation.

Alternative considered: send warm-up while `rate_limited` and promote on a 2xx response. A model- or scope-specific success would not prove that the account-wide throttle ended and would bypass the persisted cross-replica gate.

### Reuse reset evidence for normal warm-up and deduplication

After a successful recovery, the scheduler will pass the same resolved monthly before/after pair into the existing selected-window warm-up evaluation. Candidate construction therefore derives the new monthly reset tuple even after a restart, while the existing atomic attempt claim continues to enforce at most one account/window/reset attempt across workers.

Reset confirmation no longer requires the previous sample to be exhausted. A real temporal reset with newly available quota is eligible regardless of how much of the old window was used, subject to existing opt-in and availability gates. Timestamp jitter without a real boundary crossing or re-anchor remains ineligible.

Alternative considered: add a recovery-specific sender or dedupe key. That would duplicate safety checks and could create two attempts for the same reset.

## Risks / Trade-offs

- [Risk] A persisted deadline could coincidentally resemble a monthly reset. → Restrict the exception to Free accounts and require a five-second deadline match, temporal reset proof, post-block samples, fresh availability, and the 30-second floor.
- [Risk] A newer block or operator state change can race with recovery. → Guard every persisted marker in the compare-and-set and warm only after it succeeds; re-read active status in the sender.
- [Risk] Retention may remove the transition pair before a restarted scheduler observes it. → Fail closed and preserve the ordinary persisted cooldown; do not synthesize evidence from availability alone.
- [Risk] The new monthly window can be exhausted after the transition. → Require both the transition after sample and latest monthly sample to remain below 100 percent.
- [Trade-off] Recovery may perform one bounded history lookup for a selected blocked Free account. The lookup is account/window/time scoped and avoids fleet-wide work.

## Migration Plan

No data or configuration migration is required. Deploy the scheduler and warm-up changes together, then verify that qualifying Free accounts transition to `active`, clear both block markers, and create at most one monthly warm-up attempt while genuinely exhausted Plus accounts remain blocked. Rollback restores the prior conservative behavior; already recovered account rows remain valid active state and require no data repair.

## Open Questions

None.
