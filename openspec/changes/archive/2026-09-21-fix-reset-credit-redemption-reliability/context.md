# Context and investigation

## Purpose and scope

The operator reported expiring Reset credits disappearing without restored quota, unreliable automatic redemption, and the entire account list reloading after one reset. The operator also requested another cleanup of temporary `burn_first` policies, with the explicit condition that the original expiring credit was used, rather than merely disappearing.

This change implements application fixes following the operator’s request to continue. Operational policy cleanup is separate; deployment and production auto-redeem configuration changes are not part of this implementation. Requirements live in the two [delta specifications](specs/rate-limit-reset-credits/spec.md) and [frontend delta](specs/frontend-architecture/spec.md).

## Observed configuration and code

- All three inspected production replicas had automatic redemption enabled in dashboard settings, reset-credit polling enabled, a 60-second refresh interval, and usage-fetch timeout/retry settings of 10 seconds / 2 retries. Enabling the toggle again would not address the observed problem.
- The inspected scheduler scans accounts sequentially, then sleeps for approximately 60 seconds. With 1,189 eligible accounts, that is not a guarantee of refreshing each account every minute.
- Automatic redemption is hard-coded to the final five minutes. Evaluation occurs after snapshot publication, and a rejected cache generation returns before deadline evaluation.
- An automatic request is keyed by account and UTC expiry date. Finding its durable pin suppresses another attempt, although the pin is committed before the upstream consume and therefore does not prove success.
- The common consume helper updates the available-count hint after a parsed response without first classifying whether the upstream outcome confirms a reset. The automatic path lacks the manual route's outcome audit.
- A reset-credit namespace bump clears the entire reset-credit store on peer replicas. Missing snapshots are exposed as zero available resets. The frontend mutation additionally invalidates account lists, trends, reset-credit queries, and dashboard queries broadly.

Relevant implementation: `app/core/usage/reset_credits_refresh_scheduler.py`, `app/modules/rate_limit_reset_credits/{api,redeem_coordination,store}.py`, `app/main.py`, and `frontend/src/features/accounts/hooks/use-accounts.ts`.

## Evidence and limits

The preceding investigation passed 97 existing scheduler/store/API unit tests. Two local fake-clock reproductions exposed untested paths:

1. With 1,189 accounts and one second per fetch, a credit first observed with six minutes remaining is outside the current window. Its next visit is after expiry and no consume occurs. This is a simulation, not a measured production scan duration.
2. Invalidating the store while fetching a credit with one minute remaining causes the generation check to fail and skips automatic redemption entirely.

The earlier 46-account audit found 18 accounts with a missing original credit and an exhausted quota window: 12 had no observed restoration, while six had reset and subsequently consumed quota again. Missing buttons, lower credit counts, and current exhausted quota alone cannot establish whether redemption succeeded.

At 2026-09-21 00:00 UTC, the follow-up inspected all 46 original accounts. Twenty-four were already `normal`. One additional account had a quota-window reset before the original credit expiry, with the original credit absent; its policy was changed from `burn_first` to `normal`. The resulting policies were 25 `normal` and 21 `burn_first`. Among the remaining 21, four still listed the original credit and 17 no longer listed it without sufficient success evidence. The legacy snapshot did not retain credit ids, so quota restoration plus the missing old expiry is supporting evidence, not a durable receipt identifying the exact redeemed credit.

Restricted operational reports remain in the production container's `/tmp` directory; no account identities or credentials are checked into this change.

## Decisions and constraints

- Use a one-hour window, consistent with the operator's manual rescue window. This changes timing for deployments that already opted in, so settings copy and release notes in these artifacts must explain that credits may be consumed earlier. Production runtime configuration remains unchanged until deployment.
- Preserve exact-credit selection and existing PostgreSQL/SQLite serialization. A failed or uncertain request must never fall through to the next available credit.
- Distinguish confirmed reset, explicit no-reset result, terminal expiry/conflict, and unknown outcome. Do not manufacture success from a pin, missing credit, or HTTP status alone.
- Keep process-local snapshots and bounded per-replica discovery; do not introduce leader-only cache population or an external queue.
- Preserve operator-managed routing policies. Automatic redemption does not clear every `burn_first` account; this plan provides reliable credit-level outcome evidence for targeted cleanup.

## Example and failure modes

Account A has credit C1 expiring at 15:00 and C2 expiring next week. C1 enters the one-hour window at 14:00. If a consume times out, recovery keeps the same request id and C1. If a fresh read confirms C1 still available before 15:00, a bounded retry can consume C1. If C1 disappears with no authoritative receipt, the result remains unknown; C2 is untouched. If C1 is confirmed redeemed but usage refresh fails, only usage refresh is retried. An unrelated account B retains its Reset button throughout.

Upstream outages, expired credentials, process restarts, mixed-version HA rollout, lost invalidation writes, and legacy pins all need explicit regression coverage. A one-hour window improves the margin but cannot guarantee redemption during an upstream outage.

## Plan validation

Strict validation of `fix-reset-credit-redemption-reliability` passes, and proposal, design, both delta specs, and the implementation checklist are present. Implementation and focused validation are recorded in [verification.md](verification.md).

Strict validation of the current main specs reports 50 passing and 15 failing capabilities. An isolated validation of committed `HEAD` reports the same 15 failures plus `account-routing` (49 passing / 16 failing); the pre-existing workspace edits to account routing account for that difference. The remaining failures therefore predate this plan. For example, the main frontend spec has two requirements missing SHALL/MUST. These unrelated baseline failures were not edited as part of this task.
