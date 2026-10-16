## Why

Operators can inspect paused accounts and their last observed weekly reset time, but cannot inspect their reset-credit count without resuming routing. Read-only observation should not require enabling traffic or redemption.

## What Changes

- Allow on-demand dashboard reset-credit count reads for paused accounts using the existing credential refresh and account-bound proxy path.
- Preserve cached reset-credit visibility while paused; keep background polling and manual/automatic redemption disabled for paused accounts.
- Preserve paused routing exclusion and distinguish failed reads from a successful zero count through existing API error/UI unavailable handling.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rate-limit-reset-credits`: Separate dashboard observation eligibility from redemption eligibility.
- `account-routing`: Explicitly exempt paused dashboard read-only credit observation from the routing baseline.
- `frontend-architecture`: Display paused account credit counts while reset actions remain disabled.

## Impact

Account service, summary mapper, cached snapshot endpoint, API regression tests, and existing dashboard tests. No database migration, new settings, provider protocol, or production deployment.
