# Tasks

- [x] T1: Extend `AccountState` with `plan_type: str | None` and `capacity_credits: float | None` fields
- [x] T2: Add `"capacity_weighted"` to `RoutingStrategy` literal type
- [x] T3: Implement `_remaining_secondary_credits()` and `_select_capacity_weighted()` in `logic.py`
- [x] T4: Add `capacity_weighted` branch to `select_account()` dispatch
- [x] T5: Add enterprise/edu/free entries to `PLAN_CAPACITY_CREDITS_PRIMARY` and `PLAN_CAPACITY_CREDITS_SECONDARY`
- [x] T6: Populate `plan_type` and `capacity_credits` in `_state_from_account()` and `_state_for()`
- [x] T7: Change default `routing_strategy` parameter to `"capacity_weighted"` across call sites
- [x] T8: Add unit tests for capacity-weighted selection (proportional distribution, fallback, edge cases)
- [x] T9: Verify existing test suite passes without regression
