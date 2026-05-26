## Why

Accounts can stay deprioritised or blocked after an upstream quota/rate-limit window has already reset when codex-lb still has a fresh-looking usage row from the previous window. The freshness gate should not trust rows whose `reset_at` is already in the past, and blocked accounts with an expired persisted reset deadline need one immediate post-reset usage fetch even if the latest primary row is still within the normal refresh interval.

## What Changes

- Treat latest usage rows with past `reset_at` values as stale.
- Bypass the normal freshness interval for `RATE_LIMITED` and `QUOTA_EXCEEDED` accounts whose persisted reset deadline has elapsed.
- Clear stale exhausted usage percentages when account selection observes an expired reset deadline, so selection does not keep ranking the account as exhausted while waiting for the next refresh.

## Impact

- Accounts become eligible for recovery promptly after their real upstream reset deadline.
- Normal freshness throttling still applies to active accounts and blocked accounts whose reset time is still in the future.
