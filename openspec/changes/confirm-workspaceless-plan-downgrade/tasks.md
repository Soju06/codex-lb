- [x] Record a pending workspace-less paid -> `free` observation instead of
  discarding it outright, keeping the first observation non-mutating.
- [x] Persist the plan downgrade once a second consecutive workspace-less
  refresh of the same account reports the same `free` plan.
- [x] Clear the pending downgrade whenever the account reports a recognized paid
  plan again, so transient blips never accumulate.
- [x] Keep rejecting workspace-less payloads that report an unrecognized plan,
  with no confirmation path.
- [x] Leave the differing-`workspace_id` slot-conflict guard unconditional.
- [x] Add product-path regression coverage in `tests/unit/test_usage_updater.py`
  for the two-observation downgrade, the single-observation rejection, the
  paid-payload reset, the unrecognized-plan rejection, and the Force probe path.
- [x] Confirm the existing upgrade, unknown-plan hydration, workspace-mismatch,
  and taken-slot guard tests still pass.
- [x] Document the confirmation rule under the `usage-refresh-policy`
  capability.
