# Tasks

## 1. Implementation

- [x] 1.1 Confirm callers of `DashboardRepository.positive_used_percent_deltas_by_account` (dashboard overview + projections only; quota planner unaffected) and record it in the proposal.
- [x] 1.2 `app/modules/dashboard/repository.py`: `_TRAILING_DEMAND_TTL_SECONDS = 60.0`, bounded module dict keyed by the window span plus the sorted `account -> window` signature, `_clear_trailing_demand_cache()`, memoized wrapper returning a copy on hit; docstring states why the window bounds (not the span) are excluded from the key.
- [x] 1.3 `tests/conftest.py`: autouse fixture clearing the dict and zeroing the TTL so weekly-pace tests stay exact.

## 2. Verification

- [x] 2.1 Unit tests (`tests/unit/test_dashboard_trailing_demand_cache.py`): fixture reset, zero-TTL passthrough, hit on same signature (order- and window-drift-insensitive), bounds forwarded on miss, copy on hit, miss on different signature or window span, empty signature, expiry, clear, eviction at capacity.
- [x] 2.2 Integration test (`tests/integration/test_dashboard_trailing_demand_cache.py`): `/api/dashboard/overview` then `/api/dashboard/projections` issue exactly one `weekly_demand_samples` statement with the TTL active (identical pace payloads incl. the demand-derived `addProAccounts`) and two with the TTL zeroed.
- [x] 2.3 Existing `tests/integration/test_dashboard_overview.py` and `tests/unit/test_dashboard_*.py` stay green; `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, architecture/cancellation/timing/settings-tier guards, `openspec validate memoize-weekly-demand-deltas --strict`.
