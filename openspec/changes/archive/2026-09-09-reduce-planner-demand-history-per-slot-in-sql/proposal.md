## Why

Every quota planner tick (fixed 300 s cadence) loads the whole 28-day demand
history at the legacy grain — one row per `(slot, account, api_key, model,
reasoning_effort, request_kind, status)` — through the ORM and reduces it to
per-slot totals in Python. On a production deployment that is ~126,000 rollup
rows per tick; materializing them creates on the order of a million Python
objects on the serving event loop, and freeing them when the tick returns holds
the GIL for seconds. The loop-lag monitor records a 2–3 s stall every ~305 s
(300 s interval plus tick duration) that lines up to the second with the
planner decision timestamps; in-process sampling shows nothing but the C-level
teardown, plus a ~0.5 s gen-2 garbage collection the burst triggers. Every
in-flight stream on that replica stalls for the duration.

The forecast never reads the grain: `build_demand_forecast` applies
`_bin_demand_units` (a per-row `max()` of token, cost and request units) and
then sums per slot. The grain only exists so that per-row `max()` runs before
the per-slot sum.

## What Changes

- `_bin_demand_units` is compiled to SQL (`GREATEST` on Postgres, scalar
  multi-argument `MAX` on SQLite) and applied per legacy-grain row inside the
  query; the result is summed per `(slot_epoch, request_kind)` before it leaves
  the database. The rollup segment and the raw tail keep the existing
  watermark-consistent partition; the raw tail groups to the legacy grain in a
  subquery first so its per-row `max()` is preserved too.
- The planner tick and the `/api/quota-planner/forecast` endpoint consume the
  reduced shape. `build_demand_forecast` accepts pre-reduced slot units next to
  the existing bin input; both feed one per-slot map, so the forecast is
  unchanged.
- The tick logs its duration next to the event-loop lag monitor so a slow tick
  is attributable without profiling.
- `aggregate_demand_bins` stays as the exact-grain reader for parity tests;
  the parity harness gains the reduced reader as a second key, asserted equal
  across every watermark position.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quota-phase-planner`: the demand history a tick loads is bounded by slots ×
  request kinds, not by the legacy grain, and the tick reports its duration.

## Impact

- `app/modules/quota_planner/logic.py`: demand-unit coefficients become named
  constants shared with the SQL compilation; `build_demand_forecast` gains a
  `slot_units` input.
- `app/modules/accounts/usage_time_rollup_read.py`: `demand_units_sql_expr`
  and `read_demand_slot_units_window`.
- `app/modules/quota_planner/repository.py`: `aggregate_demand_slot_units`;
  the raw-tail statement is shared with the exact-grain reader.
- `app/modules/quota_planner/scheduler.py`, `api.py`: consume the reduced
  reader; tick duration log.
- No schema, config, or API surface changes.
