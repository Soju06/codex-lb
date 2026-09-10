## 1. Reconcile migration history

- [x] 1.1 Pin latest main and PR, inspect overlap and reproduce public CLI multiple-head failure.
- [x] 1.2 Add populated regression coverage for the new parents and observe red.
- [x] 1.3 Add a no-op merge revision, preserving all existing migration blobs.

## 2. Verify implementation

- [x] 2.1 Prove SQLite/PostgreSQL upgrades, both merge-only downgrade targets, retained data, roundtrip and drift.
- [x] 2.2 Verify guest/probe behavior after main reconciliation and required local checks.
- [x] 2.3 Complete independent Medium review and sync verified specs.

Hosted publication, CI/review and acknowledged watcher handoff remain delivery work tracked in the operations report directory `reports/pr2330-migration-20260910/guest-followup/`.
