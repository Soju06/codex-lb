# Context

## Reporter timeline

On October 12 at 14:30 UTC, the weekly primary quota was at 100% with a reset deadline of October 18 at 03:59 UTC. At 14:40, an upstream `429 usage_limit_reached` marked the account `RATE_LIMITED` and persisted that deadline. At 14:54, a reset credit was consumed; upstream returned `windows_reset=1`, and the account was force-refreshed. From 14:55 onward, usage reported primary usage at 0% with a new reset deadline (reported as October 20). The account nevertheless remained rate-limited for about 18 hours until manual reactivation.

The forced refresh reached `UsageUpdater._recover_quota_status_from_usage` and returned while `now < cooldown_deadline`, so the confirmed reset was discarded before the fresh usage availability checks could recover the account.

## Reproduction findings

- V1: an exactly aligned weekly reset with a post-block matching baseline recovers through scheduler reconciliation.
- V2: an unaligned replacement reset deadline does not recover.
- V3: a 429 deadline that differs from the usage reset deadline by 10 or 60 seconds does not recover.
- V4: without a post-block baseline row, recovery does not occur.
- V5: reset-credit redemption followed by forced refresh is the affected path: before this change the persisted future deadline stops recovery.
- V6: an upstream-wide scheduled weekly reset recovers when the persisted history proves the transition.

## Decision and limits

Successful redemption with `code == "reset"` and `windows_reset > 0` proves that *some* quota window reset, not that the throttle behind this 429 ended. A 429 deadline can come from a Retry-After hint, a model-scoped throttle, or a different window. So the bypass is bound to the blocked window the same way the scheduler's #2405 recovery is:

- each redemption path captures a baseline before it sends the consume request: the latest reset deadline of each quota window (primary, secondary, monthly), counting only rows from the newest usage fetch (within the 5 s sibling-fetch margin). Capturing it up front means no refresh recorded after the consume started, on any replica and whatever its clock says, can be read as the baseline; limiting it to the newest fetch keeps an older row left in another slot (for example a secondary row from before upstream moved the weekly window into the primary slot) from looking like a second matching window;
- the persisted `accounts.reset_at` must match, within five seconds, the baseline deadline of exactly one window; no match or several matches means the deadline does not identify the window, and the cooldown stays;
- the fresh snapshot must report that same window with a deadline more than five seconds away from its baseline deadline, which is the evidence that this window, not another, reset;
- the existing availability checks then run unchanged, so an exhausted window (including a still-spent short window) keeps the account blocked.

Ordering uses the time the redemption request was started, read before the consume call, not the time the response arrived: a 429 recorded while the request was in flight may postdate the reset. `blocked_at` is stored in whole seconds, so the block's second must be strictly earlier than the start time's second; a 429 in the same second as the start is conservatively kept blocked. The remaining gap is clock skew between replicas, which this does not correct. A redemption seconds after a 429 is unusual in practice (operators redeem by hand, and auto-redeem acts on credits about to expire), so the conservative edge costs little.

The dashboard redemption path also has a conditional status write keyed on the account row it loaded before the consume, so a concurrent 429 already fails that write. The scheduler's auto-redeem refresh reloads the account first, so there the start-time fence is what keeps a newer 429 blocked.

For the reporter's timeline, the 429 persisted the weekly window's own reset deadline, so the block is bound to that window and the redemption's new weekly deadline releases it. Whether the persisted value matched within five seconds was not captured in the report.

The reset-confirmation heuristic and the scheduler's five-second anchor remain unchanged. Warm-up shares `usage_reset_confirmed`, and loosening the anchor could let a reset in an unrelated window release an account blocked by another quota window.

## Known limits

All of these fail closed: the account stays `RATE_LIMITED` until its persisted cooldown ends, as before this change.

- **Secondary-only payloads.** When upstream reports no primary (5h) window and the weekly window arrives in the secondary slot, the binding can hold, but the existing `primary is None and monthly is None` guard in `_recover_quota_status_from_usage` returns first. That guard predates this change and applies to every rate-limited recovery; the reported shape is weekly in the primary slot.
- **A failed forced refresh.** The redemption evidence exists only for the one forced refresh after the consume. If that refresh fails (upstream 5xx, a route error, a failed token refresh, a workspace identity mismatch), the credit is spent and later refreshes do not carry the evidence. The scheduler's #2405 path still applies, but it needs a post-block baseline row.
- **A 429 between the account load and the redemption start.** The dashboard and accounts-API paths work from the account loaded before the redemption. If a 429 lands in between, that copy still reads `ACTIVE`, so the refresh does not release the account. The scheduler path reloads the account and is not affected.
- **Idempotent retries.** A retry after a lost consume response returns `already_redeemed`, which carries no reset confirmation, so it does not release the account even though the windows did reset.
- **A failed baseline read.** If reading the usage rows before the consume raises a database error, the redemption still goes ahead with an empty baseline. No window can then match the block, so the refresh keeps the cooldown.
- **Clock skew on `blocked_at`.** The start-time fence compares the local clock against `blocked_at` written by whichever replica recorded the 429.

The no-credit global-reset case represented by V2–V4 remains open. Diagnosing it requires the reporter's persisted `accounts.reset_at` value at block time and corresponding post-block usage history.

## Follow-up gates for #2602

This change does not resolve #2602 end to end, so the issue stays open. Each gate below is a separate piece of work, and none of them is a reason to relax the guard in this change.

- **G1 Plan identity and model eligibility.** Plan normalization belongs to #2511 / #2512. This change releases a `self_serve_business_prolite` account with the raw value still stored (integration test `test_confirmed_reset_credit_releases_account_with_reported_raw_plan_type`). Once #2512 lands, the gate is an integration scenario with the raw plan type, weekly window in the primary slot, no secondary window, release, and restored model eligibility.
- **G2 Credit-free global weekly reset (V2–V4).** This needs the reporter's persisted `accounts.reset_at` / `blocked_at` and the usage history before and after the reset. Its tests must reject releases for unrelated 429 and `Retry-After` deadlines.
- **G3 Weekly window in the secondary slot with no primary.** The `primary is None and monthly is None` guard in `_recover_quota_status_from_usage` needs its own issue and test.
- **G4 Durable redemption evidence.** This covers a failed forced refresh and an `already_redeemed` retry. It needs a recovery contract (for example evidence bound to the account and block, with a bounded lifetime) or a later-snapshot recovery shown to be safe.
- **G5 Cross-replica ordering of `blocked_at`.** This needs either a shared ordering fence or skew simulation in the acceptance tests.

## Example

A weekly-in-primary account is blocked at 14:40 with `reset_at` = Oct 18 03:59, the weekly row's deadline. A credit redeemed at 14:54 returns `windows_reset=1`; the forced refresh reports the weekly window at 0% with deadline Oct 20. The persisted deadline matches only the weekly row before the refresh, that window's deadline moved, and quota is available: the account becomes `ACTIVE`. Had the block come from `Retry-After: 3600` (deadline 15:40), no window would match and the account would stay blocked until 15:40.
