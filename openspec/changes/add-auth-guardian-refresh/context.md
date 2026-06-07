# Auth Guardian Context

## Problem

Reactive token refresh only runs when an account is selected. Accounts that are valid but idle can age until they require reauthentication. This is operationally different from an account being disabled or suspended: the account may remain usable if refresh happens before credentials become stale.

## Design

Auth Guardian is a small background scheduler that periodically refreshes active accounts whose last refresh is older than the configured max age.

Safety constraints:

- Run only on the elected leader in multi-replica deployments.
- Use background DB sessions instead of request-scoped sessions.
- Select only `active` accounts.
- Bound each pass by batch size and concurrency.
- Apply jitter and per-account failure backoff.
- Never log access tokens, refresh tokens, id tokens, API keys, or credential payloads.

## Defaults

The default interval is 6 hours and the default max refresh age is 12 hours. Operators can disable or tune the scheduler with `CODEX_LB_AUTH_GUARDIAN_*` settings.
