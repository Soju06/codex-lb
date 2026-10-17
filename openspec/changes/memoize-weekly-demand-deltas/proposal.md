# Change: memoize-weekly-demand-deltas

## Why

The weekly credit pace card needs, per account, the sum of positive `used_percent` deltas over the trailing seven days of `usage_history` (the `weekly_demand_samples` LAG window in `UsageRepository.positive_used_percent_deltas_by_account`). Production `pg_stat_statements` shows this statement at a mean of ~1.7 s and hundreds of executions per day while a dashboard is open, because it is re-run by every poll of both dashboard endpoints.

Callers (verified by grep over `app/`): only `DashboardService.get_overview` and `DashboardService.get_projections` (`app/modules/dashboard/service.py`) call `DashboardRepository.positive_used_percent_deltas_by_account`; the quota planner and the proxy hot path do not. Both callers pass the same argument shape — `_weekly_history_windows(primary_usage, secondary_usage)` (deterministic for a given latest-usage set) and a `DEMAND_WINDOW` trailing window anchored at `now` — so at the default 15 s dashboard refresh the same aggregate is computed roughly eight times per minute for a value that only moves when the usage poller appends a sample.

## What Changes

- `DashboardRepository.positive_used_percent_deltas_by_account` memoizes the aggregate in a process-local dict keyed by the window span (`until - since`, whole seconds) plus the sorted `account -> window` signature, for a fixed `_TRAILING_DEMAND_TTL_SECONDS = 60.0` (module constant, same precedent as `_COUNT_CACHE_TTL_SECONDS = 30.0` in `app/modules/request_logs/repository.py`; not a `CODEX_LB_*` setting, per the zero-config rule). The window bounds themselves are deliberately excluded from the key: both callers pass a window anchored at `now`, so within the TTL the window drifts by at most 60 s and the figure is display-only; keying on the span keeps a future caller with a different trailing window from being served another caller's entry. Hits return a copy so callers cannot mutate the cached value. The dict is bounded (16 entries, oldest-expiry eviction) and `_clear_trailing_demand_cache()` exists for tests.
- Cross-replica staleness: this cache serves a display-only figure, gates no security, authorization or routing decision, and therefore does not register a cache-invalidation namespace; its documented maximum cross-replica staleness is the 60 s TTL. Account deletion or a change of the latest-usage set changes the key, so a stale entry is at worst served for the remainder of its TTL.
- `tests/conftest.py` gains an autouse fixture that clears the dict and zeroes the TTL (mirroring `_disable_request_log_count_cache`) so existing weekly-pace tests keep exact per-request figures; cache-behaviour tests patch the TTL back to a positive value.
- Expected effect: `weekly_demand_samples` executions drop from 2 endpoints x 4 polls/min (8/min) to about 2/min per process: the frontend polls overview and projections concurrently at the same interval and there is no in-flight dedup, so on each TTL expiry both requests miss and both run the aggregate. Per-execution cost is unchanged (out of scope). Not changed: `/api/dashboard/projections` keeps returning `weekly_credit_pace` (removing it would be an API change and is a separate follow-up now that the memo dedups it).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: ADDED requirement "Weekly credit pace trailing demand is memoized per process".

## Impact

- Code: `app/modules/dashboard/repository.py` (memo + constants), `tests/conftest.py` (autouse reset fixture), new unit and integration tests.
- API/schema: none. Settings: none (settings-field ratchet unchanged). Docs: none (no published page renders this capability).
- Operators: no action; the weekly pace card may lag a fresh usage sample by up to 60 s on top of the poller interval.
