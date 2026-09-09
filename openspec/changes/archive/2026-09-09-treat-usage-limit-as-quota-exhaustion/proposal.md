## Why

An upstream `usage_limit_reached` response can describe either the primary or long quota window. Fresh usage that still reports exhaustion can incorrectly clear the selected account's recovery hold, causing repeated 429 responses even while other accounts remain usable.

## What Changes

- Preserve `usage_limit_reached` rate-limit classification and its upstream reset deadline.
- Require available primary and applicable long-window evidence before early rate-limit recovery.
- Keep an explicitly quota-exhausted account out of routing while fresh long-window usage still reports 100%, including after the quota debounce expires.
- Keep ordinary `rate_limit_exceeded` responses on the existing rate-limit cooldown path.
- Preserve pre-visible failover while preventing the exhausted account from immediately re-entering selection.
- Add full-flow regression coverage for classification, persisted health, and repeated selection on the marking replica and a peer.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Fix usage-limit recovery and explicit quota exhaustion within the existing rate-limit/quota state paths. Require applicable available usage for evidence-based early recovery; preserve ordinary `rate_limit_exceeded` cooldown and deadline expiry, and leave unrelated account-health penalties unchanged.

## Impact

- `app/modules/proxy/load_balancer.py`: recovery evidence and post-block credit freshness.
- `app/core/usage/quota.py`: explicit quota-state recovery from refreshed usage.
- Proxy HTTP, WebSocket, compact, and bridge paths that share `_handle_stream_error`.
- Account-routing unit and integration tests.
