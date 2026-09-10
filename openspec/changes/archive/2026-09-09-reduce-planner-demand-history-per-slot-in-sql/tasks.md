## 1. Reduce demand history inside the database

- [x] 1.1 Name the `_bin_demand_units` coefficients in `app/modules/quota_planner/logic.py` and compile the same formula to SQL (`demand_units_sql_expr`, dialect-aware scalar maximum) in `app/modules/accounts/usage_time_rollup_read.py`.
- [x] 1.2 Add `read_demand_slot_units_window`: one statement (state LEFT JOIN demand rollup bounded by the state row's watermark) that sums the per-row units per `(slot_epoch, request_kind)`, returning the same raw windows as the grain reader.
- [x] 1.3 Add `QuotaPlannerRepository.aggregate_demand_slot_units`; the raw tail groups to the legacy grain in a subquery (shared statement with `aggregate_demand_bins`) and sums per slot on top.

## 2. Consume the reduced shape

- [x] 2.1 `build_demand_forecast` accepts `slot_units` alongside `bins`; both feed `demand_units_by_slot_epoch` so a forecast built from either input is identical.
- [x] 2.2 The scheduler tick and the forecast endpoint call `aggregate_demand_slot_units`.
- [x] 2.3 The scheduler logs `Quota planner tick completed duration_ms=` after each leader tick.

## 3. Verification

- [x] 3.1 Unit: `demand_units_by_slot_epoch` agrees between bins and slot units (per-row `max()` before the per-slot sum, excluded request kinds, clamping); `build_demand_forecast` is identical for both inputs; the SQLite repository returns the reduced row for a seeded request.
- [x] 3.2 Parity harness: `demand_slot_units` snapshot key asserted equal across the epoch, mid-history, full-target, concurrent-fold, escape-hatch, and retention-pruned watermark states.
- [x] 3.3 Run `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, the unit suite, and `tests/integration/test_request_usage_rollup_parity.py`.
