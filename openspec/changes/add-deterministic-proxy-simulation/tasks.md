# Tasks

## 1. Clock and scheduler seams

- [x] 1.1 Add narrow clock and scheduler adapters with real production defaults.
- [x] 1.2 Thread the clock through the proxy service, load balancer, and HTTP
      bridge retry circuit seams needed by the first harness.
- [x] 1.3 Thread scheduler waits through the selected admission, startup probe,
      HTTP bridge, and WebSocket lifecycle seams.
- [x] 1.4 Thread scheduler task creation through the HTTP bridge and WebSocket
      lifecycle owners so a simulation owns every task a turn spawns.
- [x] 1.5 Declare the clock and scheduler collaborators on the HTTP bridge and
      WebSocket service protocols.
- [x] 1.6 Read the collaborators through `scheduler_for`/`clock_for` inside the
      mixins so partial service objects keep the real production default.
- [x] 1.7 Add `Scheduler.wait` and `Scheduler.fail_after` so timed multi-future
      waits and the anyio selection budget stay verbatim instead of being
      rewritten around `wait_for`.
- [x] 1.8 Re-plumb the raw timing/task sites main added after the fork
      (#1891/#1902/#1958/#1977/#1986 spawns and waits across http_bridge,
      websocket, streaming, api_key_usage, request_log) through the seam.
- [x] 1.9 Route every turn-path budget read (bridge admission, bridge/websocket/
      streaming retry budgets, websocket receive and connect deadlines) through
      the owner's injected clock so no virtual deadline is compared against the
      wall clock.
- [x] 1.10 Make the real scheduler's timing methods return the `asyncio`
      coroutine itself (no wrapper frame per relayed event) and resolve the
      collaborators once per bridge session / websocket connection instead of
      per event.
- [x] 1.11 Route the remaining turn-path spawn channels through the scheduler:
      the reconnect abort `shield(<coroutine>)` sites, the grouped-terminal
      persistence `gather`, and the deferring-cancellation helpers behind
      `_release_websocket_response_create_gate`.

## 2. Deterministic tests

- [x] 2.1 Add a virtual-clock scheduler harness under `tests/simulation/`.
- [x] 2.2 Convert selected admission and startup probe tests from real sleeps
      to virtual time.
- [x] 2.3 Convert the HTTP bridge cancel-drain idle-timeout and recovery-wait
      tests to virtual time.
- [x] 2.4 Add a seeded schedule-exploring bridge lifecycle property test that
      dispatches events as concurrent scheduler tasks.
- [x] 2.5 Add a canary proving the property checker catches a planted
      double-release bug.
- [x] 2.6 Add virtual_time regressions: non-positive `wait_for` timeouts raise
      like asyncio, `advance` moves chronologically so sequential sleeps
      complete, `wait` reports deadline-tick completions as done,
      `fail_after` raises `TimeoutError` and re-raises a racing external
      cancellation.
- [x] 2.7 Give the virtual `wait_for` and `fail_after` the shape of the real
      primitives: inline await under an `asyncio.timeout`-style deadline
      (same task, contextvars, anyio shields cut through) and a real
      `anyio.CancelScope` cancelled by the virtual timer (shields respected,
      re-delivery per checkpoint).
- [x] 2.8 Drive the production terminal handler, detach backstop and
      pre-created retry in the property turn, deliver a real task
      cancellation after the production claim, attribute each reservation
      settlement to the production path that performed it, and assert
      quiescence of every owned task.
- [x] 2.9 Add the abandoned-shield oracle to the virtual scheduler and the
      production-shaped canaries (double release on detach, lost abort
      settlement, dropped reservation release, retry reacquisition,
      re-shielded lease release) plus a coverage test over the settlement
      paths and the injected cancellation.

## 3. Verification

- [x] 3.1 Run the new and converted deterministic tests three consecutive times.
- [x] 3.2 Run strict OpenSpec validation.
- [x] 3.3 Run the full pytest suite and confirm no new failures beyond the
      documented baseline.
- [x] 3.4 Run `ruff check`, `ruff format --check`, the proxy architecture
      ratchet, and `ty check`.
- [x] 3.5 Collect `tests/simulation` in `make test-unit` so CI runs the
      harness.
- [x] 3.6 Compare each real adapter against the `asyncio`/`anyio` primitive it
      wraps (`tests/unit/test_clock_real_parity.py`).
- [x] 3.7 Run production mutants against the property test (dropped terminal
      claim, abort path skipping settlement, double admission release,
      cancel-unsafe lease release, re-introduced shield leak all caught;
      the surviving retry-ownership and non-bridge-path mutants are explained
      in the change context).
