# Tasks

- [x] 1.1 Restore `claim_unknown_operation_for_recovery` and the
      `max_recovery_dispatches` bound in `DurableBridgeRepository`, verbatim
      from `53a198d33^` — the serialized `sqlite_writer_section()` +
      `SELECT ... FOR UPDATE` over the session row and the `unknown` operation
      row, the spool deletion, and the `submitted` reset.
- [x] 1.2 Restore `restore_recovery_dispatch_claim` and both refund branches on
      `DurableBridgeRepository.mark_operation_unknown`.
- [x] 1.3 Restore the coordinator entry points:
      `DurableBridgeSessionCoordinator.claim_unknown_operation_for_recovery`
      and the `restore_recovery_dispatch_claim` pass-through on
      `mark_operation_unknown`.
- [x] 1.4 Restore the `expected_recovery_dispatch_count` parameter and its
      `where` predicate — opt-in as `int | None = None`, never a literal `0`
      default, so a row carried across an upgrade with a non-zero counter is
      not locked out of settlement — on `append_terminal_operation_event`,
      `append_terminal_operation_chunk`, `_lock_operation_for_chunk_append` and
      `settle_terminal_append_failure`, their coordinator pass-throughs, and
      the two `HttpBridgeOperationEventBatcher` methods; restore
      `DurableBridgeOperationSnapshot.recovery_dispatch_count` and its mapper.
- [x] 1.5 Restore `HttpBridgeOperationRecord.recovery_dispatch_count` in
      `app/db/models.py` and remove the `("http_bridge_operations",
      "recovery_dispatch_count")` entry (and its comment) from
      `_LEGACY_EXTRA_COLUMNS` in `app/db/migrate.py`.
- [x] 1.6 Leave `HTTPRequestState.operation_attempt_generation` and its hops in
      `_service/support.py`, `_service/http_bridge/request_submit.py`,
      `_service/http_bridge/streaming.py` and
      `_service/http_bridge/upstream_events.py` deleted; confirm with
      `grep -rn operation_attempt_generation app/ tests/`.
- [x] 1.7 Restore the deleted repository tests in
      `tests/unit/test_bridge_ring_lifecycle.py`, the
      `claim_unknown_operation_for_recovery` assertions in
      `tests/unit/test_proxy_http_bridge.py`, and the
      `expected_recovery_dispatch_count` argument in
      `tests/unit/test_http_bridge_event_batcher.py`; drop the #2366
      mixed-version tests in `tests/unit/test_db_migrate.py` and
      `tests/integration/test_migrations.py` that asserted the column is
      unmapped.
- [x] 1.8 Remove the "Drop the retired bridge recovery-dispatch column"
      next-release-queue entry from
      `openspec/specs/deployment-installation/context.md` and renumber the
      following entry back.
- [x] 1.9 Narrow the still-pending `retire-recovery-dispatch-storage` delta so
      it no longer REMOVES the restored requirement and no longer asserts the
      retired ORM mapping; only its surviving claim (fallback settlement is
      fenced by operation state plus persisted response identity, because no
      caller supplies a generation today) stays.
- [x] 1.10 Add `test_legacy_nonzero_recovery_dispatch_count_still_settles`:
      a carried-over operation with `recovery_dispatch_count == 1` settles
      through both terminal append and fallback settlement when no expectation
      is supplied, and is still refused when a stale explicit `0` is.
- [x] 1.11 Confirm the restored requirement text is byte-identical to the base
      that `relocate-anchored-turns-across-accounts` (#2374) MODIFIES, so that
      delta still applies on top.
