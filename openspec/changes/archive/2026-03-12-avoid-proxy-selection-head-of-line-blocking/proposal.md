## Why

The proxy `LoadBalancer` currently holds its in-memory runtime lock while it refreshes usage data before account selection. That refresh path can make outbound usage API calls and write refreshed rows, so one slow refresh serializes unrelated proxy traffic behind the same lock.

In practice this shows up as the GUI and proxied requests both appearing "blocked" even though the process is still alive: the selection path is waiting on refresh work while other runtime mutations and follow-on selections are queued behind the lock.

## What Changes

- move the slow pre-selection usage refresh work outside the load-balancer runtime lock
- keep the actual account-selection and runtime-state mutation step serialized under the existing runtime lock
- add regression coverage proving runtime updates are not blocked while refresh is in flight

## Impact

- Code: `app/modules/proxy/load_balancer.py`
- Tests: `tests/unit/test_proxy_load_balancer_refresh.py`
- Specs: `openspec/specs/query-caching/spec.md`, `openspec/specs/query-caching/context.md`
