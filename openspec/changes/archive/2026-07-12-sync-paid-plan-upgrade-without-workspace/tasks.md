- [x] Relax `_payload_mismatches_account_slot` so a workspace-less payload that moves the account between two recognized paid plans is not treated as a slot mismatch.
- [x] Keep rejecting workspace-less payloads that introduce `free` or an unrecognized plan for an account on a different plan.
- [x] Leave the differing-`workspace_id` conflict guard unchanged.
- [x] Add product-path regression coverage in `tests/unit/test_usage_updater.py` asserting a Plus -> Pro refresh persists the new plan for a workspace-less account.
- [x] Confirm the existing workspace-mismatch / taken-slot / free-downgrade guard tests still pass.
- [x] Document the plan-mutation trust rule under the `usage-refresh-policy` capability.

## Issue #1215 follow-up

- [x] Trust a workspace-less Free -> recognized-paid transition from the
  per-account usage payload.
- [x] Cover the Force probe refresh path while retaining paid -> Free and
  unrecognized-plan rejection.
- [x] Run 10 focused plan/workspace tests, the 11-test Force probe suite, the
  remaining 81 usage-updater tests, Ruff, `ty`, strict change/all-spec
  validation, and `git diff --check`; the excluded Windows-local monthly-row
  freshness test reproduces unchanged on `upstream/main`.
