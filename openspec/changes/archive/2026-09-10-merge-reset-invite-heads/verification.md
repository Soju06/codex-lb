# Verification

Target: 561311ded1d3191cf1ef271d4cd8ea97f8fd17a4. Previous published candidate: 8a727d20a2a8c810879d0dd1d2d7a578898bf8a5.

Public upgrade-head and both populated parent cases failed with MultipleHeads before the new merge. No published migration was changed. The new 050000 merge joins the published reset/user 040000 and main invitation 040000 revisions. Adjacent import conflicts in app/main.py and app/dependencies.py retain both Desktop and user-management registration paths.

The existing PostgreSQL database guard was moved without behavioral changes into a shared fixture. The separate invitation test preserves complete pending, consumed and revoked invite rows, token hashes, flags, inviter snapshots and timestamps. It compares user/role/grant, identity, audit, generation, setting and redemption data through upgrade and merge-only rollback/re-upgrade. The historical user merge test targets its original 040000 merge before checking latest-head drift.

A focused combined run passed 59 tests with 12 PostgreSQL skips and exposed one stale main test requiring the invitation revision itself to remain head. Its final assertion now checks current single-head ancestry, preserving direct upgrade/idempotency/downgrade/table checks; its focused rerun passed. All 12 PostgreSQL cases across both migration files passed on an isolated PostgreSQL 16 database. Both database variables named the same dedicated codex_lb_test database before imports. The test container was removed afterward. These runs overlap and must not be summed.

CLI reports one head, migration policy ok and no schema drift. Main's user/invite management, role reads, session behavior and CSRF tests run alongside public reset authorization controls. No policy changes or real credit use occurred. Makefile explicitly includes both migration files in hosted PostgreSQL selection.

Final-head hosted CI and actual review remain pending and will be recorded after publication in a separate immutable PR receipt.

Ruff, architecture, settings checks, full typing, strict change validation and all 67 main specifications passed. Independent Medium composition review and the separate ancestry-assertion addendum found no issues or weakened assertions. Published migration blobs remain unchanged.
