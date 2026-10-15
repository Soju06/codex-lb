# Tasks

> Tasks 1.2, 1.3, 1.6, 1.7 and 1.9 — and the repository/coordinator/batcher half
> of 1.4 — were reverted by `restore-recovery-dispatch-claim`. Task 1.5 (the
> request-state plumbing) and the `upstream_events` call sites from 1.4 stand.

- [x] 1.1 Prove no writer can advance `recovery_dispatch_count` on `main`:
      grep every occurrence across `app/`, `tests/`, `frontend/`, `scripts/`,
      `deploy/`, `openspec/` and confirm the only increments/decrements are
      inside `claim_unknown_operation_for_recovery` and the
      `restore_recovery_dispatch_claim` branches, both callerless.
- [x] 1.2 Delete `claim_unknown_operation_for_recovery` and the
      `max_recovery_dispatches` bound from the repository and the coordinator.
- [x] 1.3 Delete the `restore_recovery_dispatch_claim` flag and its refund
      branches from `mark_operation_unknown` (repository + coordinator),
      keeping the SUBMITTED -> UNKNOWN transition.
- [x] 1.4 Remove `expected_recovery_dispatch_count` from the three CAS sites,
      their coordinator/batcher pass-throughs and the `upstream_events` call
      sites; keep the owner, `abandoned`, `spool_format` and
      operation-state/response-identity predicates.
- [x] 1.5 Delete `HTTPRequestState.operation_attempt_generation` and its
      propagation in `request_submit.py` / `streaming.py`.
- [x] 1.6 Remove the `recovery_dispatch_count` ORM mapping and allow-list the
      retained physical column in `_LEGACY_EXTRA_COLUMNS`.
- [x] 1.7 Cover the mixed-version contract: head schema still carries the
      column, the ORM does not map it, a legacy-style INSERT with an explicit
      value succeeds, a current-release ORM INSERT defaults it to 0, and
      `check_schema_drift` is clean.
- [x] 1.8 Replace the deleted repository tests: prove the operation-state
      fence (not a durable generation) rejects a prior attempt's delayed
      fallback settlement after a rebind to SUBMITTED.
- [x] 1.9 Queue the physical drop in
      `openspec/specs/deployment-installation/context.md` under the
      next-release entry titled "Drop the retired bridge recovery-dispatch
      column" (cited by title, not by list position).
