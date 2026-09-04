## Why

An upstream `usage_limit_reached` response proves that the selected account has exhausted usable quota, but codex-lb currently applies only the short generic rate-limit cooldown. A fresh usage sample can then reactivate the same still-exhausted account almost immediately, causing repeated 429 responses even while other accounts remain usable.

## What Changes

- Classify `usage_limit_reached` as quota exhaustion for account-health handling.
- Keep an explicitly quota-exhausted account out of routing while fresh long-window usage still reports 100%, including after the quota debounce expires.
- Keep ordinary `rate_limit_exceeded` responses on the existing rate-limit cooldown path.
- Preserve pre-visible failover while preventing the exhausted account from immediately re-entering selection.
- Add regression coverage for the classification and account-health mutation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Distinguish upstream usage exhaustion from transient rate throttling when applying account-scoped health penalties.

## Impact

- `app/modules/proxy/helpers.py`: upstream failure classification.
- `app/core/usage/quota.py`: explicit quota-state recovery from refreshed usage.
- Proxy HTTP, WebSocket, compact, and bridge paths that share `_handle_stream_error`.
- Account-routing unit and integration tests.
