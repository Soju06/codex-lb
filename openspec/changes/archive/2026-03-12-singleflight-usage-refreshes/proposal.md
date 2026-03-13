## Why

The proxy no longer blocks unrelated traffic behind the load-balancer runtime lock, but stale usage windows can still trigger duplicate refresh work. When several requests or the background scheduler notice the same account is stale at about the same time, each caller can still fetch usage and write the same window rows independently.

That wastes upstream usage API capacity and adds avoidable write load right at the refresh boundary.

## What Changes

- add per-account singleflight coordination around stale usage refreshes
- re-check the latest persisted primary-window row before issuing an upstream usage fetch so a follower can skip duplicate work after another caller refreshed
- keep callers synchronized with the persisted account state after shared refresh work completes

## Impact

- Code: `app/modules/usage/updater.py`, `app/modules/usage/repository.py`, `app/modules/accounts/auth_manager.py`
- Tests: `tests/unit/test_usage_updater.py`
- Specs: `openspec/specs/query-caching/spec.md`, `openspec/specs/query-caching/context.md`
