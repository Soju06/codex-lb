## 1. Regression Coverage

- [x] 1.1 Add focused unit coverage that proves header failure or cancellation
  releases an owned reservation once across a cancellation checkpoint,
  preserves the original failure if release persistence fails, and does not
  release a borrowed reservation.
- [x] 1.2 Add one parameterized ASGI regression for stream, collect, compact,
  and transcribe that injects header failure after real quota admission and
  requires exactly one release, a `released` row, restored quota, and no
  upstream call.
- [x] 1.3 Run the new regressions against the original implementation and
  confirm they fail at the missing cleanup handoff.

## 2. Reservation Ownership Fix

- [x] 2.1 Add one narrow helper that retains owned-reservation cleanup through
  rate-limit header preparation, releases on any unsuccessful exit, and
  re-raises the original failure.
- [x] 2.2 Route streaming Responses, collected Responses, compact Responses,
  and subscription-backed transcription through the helper without changing
  source, image, chat, forwarded, or downstream settlement behavior.
- [x] 2.3 Include the route regression in the focused PostgreSQL test target so
  the commit-to-separate-session-release path runs on asyncpg in required CI.

## 3. Verification

- [x] 3.1 Run the new unit and ASGI regressions plus focused existing API-key
  reservation, forwarded-owner, compact, and transcription lifecycle tests.
- [x] 3.2 Run the applicable PostgreSQL proof when locally available, affected
  Ruff/format/type checks, and the proxy architecture checker.
  - Ruff, format, scoped `ty`, and proxy architecture checks passed. No local
    PostgreSQL listener was available on TCP 5432; the regression is included
    in the required PostgreSQL CI target instead.
- [x] 3.3 Run strict scoped OpenSpec validation, all main-spec validation,
  OpenSpec verification, final diff review, and worktree-status inspection.
