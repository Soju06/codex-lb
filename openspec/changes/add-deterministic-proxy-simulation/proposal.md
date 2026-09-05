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
  invariants that drives the production terminal, detach and retry paths under
  real injected cancellation, plus production-shaped canaries that prove the
  checker catches planted double-release, lost-settlement, dropped-release,
  retry-reacquisition and shield-leak bugs.

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
- Disclosed deltas, listed in the PR body: the startup-probe and request-log
  rewrite deadlines read `time.monotonic()` instead of
  `asyncio.get_running_loop().time()` (a different clock source under uvloop;
  each deadline's two sides share it); sticky/unbound selection and the
  account state derivation it drives (`_state_from_account`,
  `_usage_entry_is_recent_enough`) sample the epoch once under the runtime
  lock instead of several fresh reads; shared helpers that used to sample the
  clock themselves (`waited_seconds` keepalive payloads, TTFT visibility
  stamps, `last_upstream_activity_at`, the bridge/websocket/streaming
  remaining-budget receivers) now take the caller's sample from the same owner
  clock, microseconds earlier and never a different clock under the real
  defaults; the three request-state constructors stamp the two touch fields
  from the owner clock instead of the wall-clock default factory; and the
  budget arithmetic moved to `_service/clock_budget.py` so
  bridge/websocket/streaming budgets read the injected clock.
