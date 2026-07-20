# Tasks

- [x] Add `StickySessionsRepository.purge_stale_hard_codex_session_mappings`,
      gated strictly on account status (`PAUSED`, or
      `RATE_LIMITED`/`QUOTA_EXCEEDED`) and elapsed time past
      `blocked_at`/`reset_at`.
- [x] Wire it into `StickySessionCleanupScheduler`'s existing periodic
      leader-elected cycle with a fixed, deliberately conservative threshold.
- [x] Add regression coverage: a fresh (recently rate-limited/paused) owner's
      mapping survives; a durably-unavailable owner's mapping is purged; a
      healthy owner's mapping is never touched.
- [x] Add scheduler-level coverage that the new purge call happens once per
      cycle with the expected threshold.
- [x] Update the pre-existing scheduler test whose docstring asserted
      `codex_session` mappings are "never purged" by this job.
- [x] Run focused and full test suites, ruff check/format, `ty check`.
