## Why

Proxy turn lifecycle regressions repeatedly come from timing-dependent
interleavings: admission waits, upstream terminal events, downstream
cancellation, retries, and lease release ownership. Existing tests cover many
of these paths, but several rely on short real sleeps and scheduler jitter.

## What Changes

- Add production clock and scheduler adapters with real defaults.
- Thread those adapters through the first audited proxy lifecycle seams.
- Add a virtual-clock test harness that can drive selected proxy tests without
  wall-clock sleeps.
- Add a seeded schedule checker for bridge-turn terminal and lease-release
  invariants, plus a canary that proves the checker catches a planted
  double-release bug.

## Impact

- Affected specs: `deterministic-proxy-simulation`
- Affected code: proxy clock/scheduler injection seams and deterministic tests.
- No new dependency, setting, migration, or dashboard surface.
- No production behavior change: every real adapter delegates verbatim to
  `asyncio`/`anyio` (`RealScheduler` has no task registry and adds no
  timeout; `tests/unit/test_clock_real_parity.py` compares each adapter
  against the primitive it wraps), and production code keeps main's control
  flow with only `asyncio.X` -> `scheduler.X` / `time.monotonic()` ->
  `clock.monotonic()` substitutions.
- Disclosed deltas, listed in the PR body: the startup-probe deadlines read
  `time.monotonic()` instead of `asyncio.get_running_loop().time()` (a
  different clock source under uvloop; both deadline computations share it),
  sticky/unbound selection samples the epoch once under the runtime lock
  instead of several fresh reads, and the budget arithmetic moved to
  `_service/clock_budget.py` so bridge/websocket/streaming budgets read the
  injected clock.
