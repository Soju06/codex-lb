# Verification notes

## Base

- Branch `takeover/sim-harness-guard`, cut from `takeover/sim-harness` at
  `847afd41eebd076d2a16a3d2637929365db3a76e` (PR A, takeover of #1647).
- The gate depends on PR A's injection work: on `main` before PR A the
  `--report` table would list dozens of turn-path modules; the committed
  block describes PR A's final tree.

## Archive order

Both `add-deterministic-proxy-simulation` (PR A) and this change add
requirements under `proxy-architecture`-adjacent specs, and
`prevent-level-cancellation-busy-spin` (#1958) adds a requirement to
`proxy-architecture` itself. Archive PR A first, then #1958's change, then
this one, or record the order taken; the requirement names do not collide, so
any order validates, but the spec's requirement list should follow landing
order.

## Passing checks (worktree, `.venv` of the main clone)

- `python scripts/check_proxy_timing_seams.py`: `proxy timing seam checks passed`.
- `python scripts/check_proxy_architecture.py`, `python scripts/check_cancellation_safety.py`: passed.
- `pytest tests/unit/test_check_proxy_timing_seams.py tests/unit/test_check_proxy_architecture.py tests/unit/test_check_cancellation_safety.py`: 98 passed.
- `ruff check scripts tests/unit/test_check_proxy_timing_seams.py`, `ruff format --check`: clean.
- `ty check scripts/check_proxy_timing_seams.py tests/unit/test_check_proxy_timing_seams.py`: passed.
- `openspec validate enforce-proxy-timing-seams --strict`: passed.

## Negative proof

Inserting `await asyncio.sleep(0.5)` after the `scheduler.sleep(chunk_seconds)`
call in `app/modules/proxy/_service/http_bridge/streaming.py` produced:

```
proxy timing seam check failed: app/modules/proxy/_service/http_bridge/streaming.py:739: raw-sleep asyncio.sleep(...); use scheduler_for(owner).sleep(...); only a literal sleep(0) yield point stays raw
proxy timing seam check failed: app/modules/proxy/_service/http_bridge/streaming.py has 1 raw timing sites; allowance is 0
```

The probe was reverted; the committed tree passes.

## Residual census (from `--explain` on the PR A tree)

Timing (33): `api.py` 19 route-layer deferring-cancellation calls without
`scheduler=`; `realtime_live.py` 5 (relay/close owners); `compact.py` 3;
`http_bridge_event_batcher.py` 2 (lazy flusher); `http_bridge/mixin.py` 1
(shutdown drain `wait_for`); `request_log.py` 1 (shutdown drain timed
`wait`); `shared_future.py` 1 (`ensure_future` fallback).

Clock (46): `api.py` 8 (payload epochs, images `perf_counter`); `compact.py`
6; `load_balancer.py` 3 (`REAL_CLOCK` default idioms); `websocket/helpers.py`
3 (stale previous-response TTL cache); 2 each in `codex_control.py`,
`file_ops.py`, `transcribe.py` (`_service_time()` latency stamps),
`rate_limit.py`, `realtime_live.py`, `request_log.py` (`loop.time()` in the
drain), `support.py` (upstream-WS transport failure TTL), `warmup.py`,
`account_cache.py`, `rate_limit_cache.py`, `durable_bridge_repository.py`,
`images_service.py`; 1 each in `clock_budget.py` (module-level legacy budget
seam), `http_bridge/helpers.py:820`, `account_eligibility.py:45` (default
idiom), `images_observability.py`.
