## 1. Recovery schema repair

- [x] 1.1 Restore missing recovery columns, indexes, and retained-alias target
      idempotently for databases stamped at the deployed head.
- [x] 1.2 Preserve repaired parent-owned objects on downgrade and migrate
      legacy revision-local ownership markers to historical owners.
- [x] 1.3 Keep the continuity migration as a sibling branch and converge the
      graph through a metadata-only merge.

## 2. Verification

- [x] 2.1 Cover repair, downgrade, marker re-homing, and schema-drift parity
      with migration tests.
- [x] 2.2 Run migration topology, lint, format, and type checks.
