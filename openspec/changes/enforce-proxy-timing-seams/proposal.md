## Why

The deterministic proxy turn-lifecycle harness (`add-deterministic-proxy-simulation`,
takeover of #1647) only holds while every timer and task a turn creates goes
through the `Scheduler`/`Clock` seams in `app/core/clock.py`. The original PR
rotted exactly this way: between its fork and today `main` grew from 18 to 84
raw `asyncio`/`time` sites under `app/modules/proxy`, none of which failed a
test, because a raw `asyncio.sleep` or `time.monotonic()` on a simulated path
simply puts that path back on the wall clock. The harness invariant needs a
gate in the required lint target, not a review checklist.

## What Changes

- Add `scripts/check_proxy_timing_seams.py`, an AST gate over every module
  under `app/modules/proxy/` plus `app/core/utils/shared_future.py`, with five
  rules: `raw-sleep`, `raw-timeout`, `raw-task-spawn`, `raw-clock-read` and
  `missing-scheduler-kwarg` (a call to a listed owner-less seam function that
  omits its `scheduler=`/`clock=`/`scheduler_owner=` keyword).
- Own the gate's configuration in a second marked TOML block in
  `openspec/specs/proxy-architecture/spec.md`: `[scheduler_kwarg_required]`
  (function name -> keyword or list of keywords) and per-module
  `[allowances.timing]` / `[allowances.clock]` counts. Unlisted modules have
  an allowance of zero; there is no inline marker or label override.
- Seed the allowances from the harness tree (`--report`), and pin them exactly
  in `tests/unit/test_check_proxy_timing_seams.py` so removing a raw site must
  lower its number in the same diff while the script itself keeps the
  `<=` ratchet semantics of the other architecture gates.
- Wire the gate into `make architecture-check`, which `make lint` and the CI
  `Lint (ruff)` job already run.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `proxy-architecture`: adds the timing-seam requirement and its OpenSpec-owned
  configuration block.

## Impact

- Code: `scripts/check_proxy_timing_seams.py`, `Makefile`
  (`architecture-check`), `openspec/specs/proxy-architecture/spec.md`
  (configuration block only), `tests/unit/test_check_proxy_timing_seams.py`.
- No production code changes: the committed allowances describe the tree as
  it is. The residual sites are other endpoints (compact, realtime relay,
  images, transcribe, file ops, codex control), background infrastructure
  (durable operation-event batcher, request-log shutdown drain, HTTP bridge
  shutdown drain), process-global TTL caches, telemetry stamps, the
  `now is None` default idioms in the load balancer and eligibility helpers,
  the legacy module-level `_remaining_budget_seconds` reserved for exempt
  endpoints, the sanctioned `ensure_future` fallback in the shared-future
  helper, and the `app/modules/proxy/api.py` route layer, which calls the
  deferring-cancellation helpers with their real default because no
  `ProxyService` is in scope.
- Depends on the `takeover/sim-harness` PR (`add-deterministic-proxy-simulation`).
