# Maintenance takeover verification

Cleanup failures must not strand the maintenance lock or the replacement's
exclusive SQLite transaction. The outermost resource cleanup therefore runs
regardless of rollback or temporary-directory removal errors. A regression
injects a cleanup failure after installation and verifies immediate read/write
access to the compacted database.

Replacement recovery takes the shared maintenance lock before checking source
integrity or building an export, so a competing compaction is rejected before
recovery can contend for SQLite locks or leave a completed output file.

SQLite's `SQLITE_TMPDIR` and POSIX `TMPDIR` remain process/library conventions.
Their reads live in the configuration module; no new CODEX_LB setting or
configuration-tier allowlist exception is added. The existing disk-space
regression uses SQLITE_TMPDIR to verify the target filesystem's free space.

On the 2026-09-10 current-main integration, both new regressions failed before
the fixes. Afterward, 106 compaction, recovery, and settings-reference tests
passed, along with full Ruff/typing, architecture/cancellation/timing/tier
checks and strict validation of this change. All 65 canonical specs validated.
Tests use disposable databases and do not compact a running deployment.

Descriptor acquisition now compares `fstat` device/inode with the initial source stat before any integrity or compaction work. The regression supplies a descriptor to a different inode while retaining the source path; it failed before the fix and passes afterward without modifying either database.
