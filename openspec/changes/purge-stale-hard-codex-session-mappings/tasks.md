# Tasks

- [x] Add `StickySessionsRepository.purge_stale_hard_codex_session_mappings`,
      gated on an unavailable account status and the conservative hard-mapping
      clock (later of last use and transition into unavailability).
- [x] Refresh that clock when an owner first becomes unavailable without
      extending it on repeated unavailable-status writes.
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
