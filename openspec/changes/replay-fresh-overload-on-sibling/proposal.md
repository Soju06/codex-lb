# Change: replay-fresh-overload-on-sibling

## Why

A fresh, self-contained HTTP Responses request can receive the upstream
`server_is_overloaded` terminal as either its first SSE event or after only
`response.created` / `response.in_progress`.  The direct HTTP stream currently
forwards those lifecycle frames and the terminal before its retry owner can
classify the failure.  The request is therefore downstream-visible and the
already-enabled deterministic failover policy cannot exclude the rejected
account.  A soft prompt-cache affinity can then send several independent fresh
requests to that same account until the separate three-rejection overload
backoff changes later selection.

## What Changes

- Hold only the accepted lifecycle prelude of a replay-safe fresh HTTP stream
  until the first output or terminal event.
- Route an output-free `server_is_overloaded` or `overloaded_error` terminal
  back to the existing bounded account-exclusion path while deterministic
  failover is enabled.
- Keep this exception unavailable to a request with a previous-response,
  turn-state, file, single-account, dispatched-payload, or other hard owner;
  and unavailable after content, tool output, or terminal output evidence.
- Reuse the existing account exclusion, lease release, health settlement, and
  `overload_backoff` paths.  Disabling deterministic failover remains
  fail-closed.  No caller retry loop, timeout, or model routing is changed.

## Impact

- Code: `app/modules/proxy/_service/streaming/mixin.py` and its existing retry
  owner in `app/modules/proxy/_service/streaming/retry.py`.
- Tests: `tests/unit/test_proxy_utils.py`.
- No schema, setting, deployment, or upstream protocol change.
