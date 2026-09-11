## Why

`retire-recovery-dispatch-storage` (#2366) deleted the durable
recovery-dispatch claim on the grounds that it is callerless. That premise was
true for `main` at the time only because `drop-bridge-recovery-modes` (#2336)
had deleted the caller, and the owner decided on 2026-09-11 to add one back:
`relocate-anchored-turns-across-accounts` (#2374) restores at-least-once
relocation for ambiguous eventless dispatches and uses
`claim_unknown_operation_for_recovery` as its one-shot duplicate-suppression
gate. #2374 also **MODIFIES** the same `responses-api-compat` requirement
#2366 **REMOVES**, so the two deltas collide head-on.

Re-deriving an atomic claim — a serialized `FOR UPDATE` over the session row
and the operation row, plus the spool reset and the refund branches — is the
part of this surface that is expensive to get wrong, so it is restored as it
was rather than rewritten. The pieces that are dead whether or not the fence
has a caller stay deleted.

## What Changes

- **Restore the durable one-shot claim.**
  `DurableBridgeRepository.claim_unknown_operation_for_recovery` with its
  `max_recovery_dispatches` bound and the `restore_recovery_dispatch_claim`
  refund branches on `mark_operation_unknown`, byte-identical to their
  pre-#2366 form, plus the `DurableBridgeSessionCoordinator` pass-throughs.
- **Restore the `expected_recovery_dispatch_count` CAS parameter surface**, as
  an opt-in `int | None = None` rather than the pre-#2366 `int = 0`, on
  `append_terminal_operation_event`, `append_terminal_operation_chunk`,
  `_lock_operation_for_chunk_append` and `settle_terminal_append_failure`
  (repository), their coordinator pass-throughs, and
  `HttpBridgeOperationEventBatcher.append_terminal_event` /
  `settle_terminal_event`, together with
  `DurableBridgeOperationSnapshot.recovery_dispatch_count`.
- **Restore the ORM mapping.**
  `HttpBridgeOperationRecord.recovery_dispatch_count` is mapped again and the
  column leaves `_LEGACY_EXTRA_COLUMNS` in `app/db/migrate.py`. The physical
  column was retained by #2366, so no migration is involved either way.
- **Restore the repository tests** #2366 deleted:
  `test_unknown_operation_recovery_claim_is_atomic_and_single_use`,
  `test_one_shot_recovery_budget_survives_unknown_reset`,
  `test_pre_dispatch_recovery_claim_restores_one_shot_budget`, the
  unknown-claim half of
  `test_chunk_format_resets_on_failed_rebind_and_unknown_claim`, the
  generation-fence block of
  `test_terminal_append_failure_settlement_is_visible_to_recovery`, and the
  `claim_unknown_operation_for_recovery` assertions in the abandoned-fence
  tests.
- **Keep deleted:** `HTTPRequestState.operation_attempt_generation` and every
  hop that carried it — the `request_submit` seed, the two
  `_service/http_bridge/streaming.py` carry-overs, and the two
  `_service/http_bridge/upstream_events.py` call-site arguments. Those hops
  only ever carried a constant `0` and #2374 rewires the expectation from its
  own claim site, so re-adding them now would re-add dead code.
- **Un-queue the column drop.** The next-release-queue entry "Drop the retired
  bridge recovery-dispatch column" is removed from
  `openspec/specs/deployment-installation/context.md`: the column is staying.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the durable one-shot recovery-dispatch budget and
  its atomic claim/refund contract return as a requirement, restoring the base
  text that `relocate-anchored-turns-across-accounts` (#2374) modifies.

## Impact

- `app/db/models.py`, `app/db/migrate.py`,
  `app/modules/proxy/durable_bridge_repository.py`,
  `app/modules/proxy/durable_bridge_coordinator.py`,
  `app/modules/proxy/http_bridge_event_batcher.py` and their tests.
- **No behaviour change on this branch.** The restored expectation is opt-in
  (`int | None = None`, predicate applied only when supplied) and no caller
  supplies it until #2374 lands, so no restored predicate is added to any
  statement. What changes is that the primitive, its tests and its requirement
  exist for #2374 to wire up.
- No Alembic revision: #2366 kept the physical column, and this change keeps
  it too.
- No new setting, no new default.
