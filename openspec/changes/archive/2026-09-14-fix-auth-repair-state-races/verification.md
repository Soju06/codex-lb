## Scope and Evidence

Maintainer comment: https://github.com/Soju06/codex-lb/pull/2132#issuecomment-5662645288

Original reviewed head: `437eaa2e8f081488ad4f56ac33e764a4a95d9b33`, against `ca243dec437d82c407ff2da8f64b61e0a0189e43`. Independent static review found no additional defects beyond the reported cache, cooldown, and encryptor concerns. The original head retains those known defects and is not described as ready.

## Regression Results

- Stale snapshot publication before the local rejection mark reproduced failure for seeded and unseeded caches. The fixed cache suite passed 32 tests, including same-account repair fencing through refresh/reset and no-poller stale bridge refusal.
- Real database cooldown/rejection/rotation/selection regression reproduced early selection before repair. Rotation, rejection-CAS, and token-refresh-claim suites passed 78 tests; the final three-case credential matrix passed after fixture refinement.
- Caller-owned encryptor plumbing passed 386 consumer/auth tests with three pre-existing per-account-locking skips. These include reset-credit HTTP behavior and valid/unknown/expired/rejected access eligibility.
- Cache, dashboard overview, usage scheduler recovery, and token refresh claims passed 146 tests.
- Final independent patch review found another cooldown-loss path when the first rejection CAS succeeds with a current rate-limited row and cold runtime. The rejection handler now preserves both persisted timestamps before that write. A free-plan/fresh-monthly-usage regression reproduced the loss, then passed all six current/stale and repaired/unchanged/re-encrypted cases. Combined rotation, rejection CAS, cache, multi-replica, and load-balancer suites passed 189 tests with three existing skips. The reviewer rechecked this correction with no further confirmed actionable finding.
- Full `make lint typecheck` passed, including architecture, cancellation, timing, settings tiers, migration topology, Ruff, formatting, and ty. All 65 canonical specs passed strict validation.

## Review Limitations

The independent reviewer noted an unexecuted PostgreSQL/concurrent-peer hypothesis around a new rejection occurring after token-rotation commit but before its local cache clear. File-backed SQLite serializes same-process writes through that interval. This follow-up does not claim PostgreSQL race coverage or treat that hypothesis as an executed reproduction.

## CI and Coordination

Original CI run `34809162515`, attempt 1, failed only the integration-core-1 stall-abandonment test: upstream pending time was 0.252 seconds versus its 0.3-second evidence threshold. The unchanged test passed locally. GitHub refused `gh run rerun --failed` because the caller lacks repository-admin rights. Local success does not establish green cloud CI; the authorized push will trigger a fresh run.

PRs #2117 and #2391 remain open and overlap this work. Their ordering and the proposed consumer-gate split are left to the maintainer. This follow-up does not merge or rewrite them, change the bundle branch, restart services, or alter schema/configuration.
