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
- No new dependency, setting, migration, dashboard surface, or production
  behavior change.
