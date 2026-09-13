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
- [x] Let a known, future `Account.reset_at` override the flat cutoff so a
      mapping survives until after its owner's own stated recovery point.
- [x] Add `AccountsRepository.seed_hard_sticky_outage_grace_on_startup`,
      called once during app boot, so an account already unavailable before
      this process started doesn't get its mapping treated as ancient by the
      first cleanup cycle after deploy.
- [x] Add regression coverage for the `reset_at` override and the startup
      seeding method.
- [x] Run focused and full test suites, ruff check/format, `ty check`.
- [x] Add `StickySession.continuity_abandoned_at` (migration
      `20260727_000000_add_sticky_session_continuity_abandoned_at`) and make
      the purge tombstone (set the column) before ever deleting a mapping
      outright, so a `conversation`-continuity request against a purged key
      isn't stranded permanently once the pool has more than one account.
- [x] Add `StickySessionsRepository.get_account_id_and_abandonment` and use
      it in `run_sticky_selection_path` to exempt a tombstoned mapping from
      the ambiguous-owner fail-closed check.
- [x] Clear `continuity_abandoned_at` in `StickySessionsRepository.upsert`
      and `restore_if_current` so re-establishing a hard owner fully
      restores normal (non-abandoned) behavior for that key.
- [x] Extend `purge_stale_hard_codex_session_mappings` to hard-delete a
      tombstone once it's sat unclaimed past a further grace window.
- [x] Replace the every-boot reseed in
      `AccountsRepository.seed_hard_sticky_outage_grace_on_startup` with a
      one-time backfill gated on a durable `runtime_sentinels` marker, so a
      shared-database multi-replica deployment only ever seeds once, not on
      every restart.
- [x] Add regression coverage: a tombstoned mapping unblocks a
      `conversation`-continuity request instead of failing closed; the
      tombstone-then-delete phases (survive while claimed/within grace,
      deleted once unclaimed past it); the startup seeding method is a no-op
      on a second boot.
- [x] Re-run focused and full test suites, ruff check/format, `ty check`,
      and the architecture-check script after these fixes.
