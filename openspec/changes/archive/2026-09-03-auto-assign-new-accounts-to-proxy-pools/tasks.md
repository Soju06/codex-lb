## 1. Balanced assignment policy

- [x] 1.1 Add a focused upstream-proxy helper that selects an active,
  structurally usable pool with the fewest active bindings and a deterministic
  tie-break; verify focused tests cover candidate filtering and balanced
  sequential selection.
- [x] 1.2 Serialize concurrent automatic selection on PostgreSQL while using
  the existing SQLite writer transaction; verify concurrency-sensitive tests
  or query assertions cover the lock boundary.

## 2. Account creation integration

- [x] 2.1 Integrate automatic assignment only into the new-row branch of
  account slot upsert, commit account and binding atomically, and invalidate
  the upstream-route cache after commit; verify repository tests cover new
  creation, no usable pool, and re-import preservation.
- [x] 2.2 Verify both `auth.json` import and untargeted OAuth creation use the
  shared behavior while targeted reauthentication preserves its binding.

## 3. Validation

- [x] 3.1 Run focused Python tests and lint/type checks for changed modules.
- [x] 3.2 Run strict OpenSpec validation and confirm the change artifacts and
  implementation remain coherent.
