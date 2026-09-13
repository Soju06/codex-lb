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
- [x] Restrict confirmation to workspace-less accounts so a workspace-bound
  seat is never downgraded by a payload that omits `workspace_id`.
- [x] Persist pending observations in a per-account table so the observation
  sequence is coherent across replicas sharing one database.
- [x] Add a guarded, idempotent Alembic revision on the current single head, with
  a matching downgrade and no data backfill.
- [x] Pin each observation to a non-secret digest of the account's stable seat
  identity, and discard pending evidence in the same transaction whenever fresh
  credential material is applied to an existing row (re-import or in-place
  reauthentication); the foreign key removes evidence when the account is
  deleted.
- [x] Cover the cross-replica and replaced-credential paths with unit regression
  tests and product-path integration tests, including durable-state assertions.
- [x] Digest stable seat identity rather than token material, so routine token
  rotation — and re-encryption, key rotation, or an undecryptable credential
  row — never reads as a credential replacement and never resets pending
  evidence.
- [x] Record observations atomically so concurrent refreshes of one account cannot
  lose an increment.
- [x] Gate the paid-payload evidence clear on a cheap existence check so healthy
  accounts stay read-only on the refresh hot path.
- [x] Run the observation store's raw SQL, datetime binds, concurrent upserts,
  and missing-schema degrade against PostgreSQL in CI via the postgres test
  allowlist, not only SQLite.
- [x] Add regression coverage for token rotation between observations, in-place
  reauthentication, and overwrite re-import at the store, repository, and
  product-path levels.
