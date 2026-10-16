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

Successful redemption with `code == "reset"` and `windows_reset > 0` is direct upstream confirmation that a quota window reset. Its completion time is passed only into the resulting forced refresh. Recovery still requires the fetched quota to have availability; an exhausted window remains blocked. The marker must postdate `blocked_at`, preventing an earlier redemption from releasing a later block.

The reset-confirmation heuristic and five-second anchor remain unchanged. Warm-up shares `usage_reset_confirmed`, and loosening the anchor could let a reset in an unrelated window release an account blocked by another quota window.

The no-credit global-reset case represented by V2–V4 remains open. Diagnosing it requires the reporter's persisted `accounts.reset_at` value at block time and corresponding post-block usage history.
