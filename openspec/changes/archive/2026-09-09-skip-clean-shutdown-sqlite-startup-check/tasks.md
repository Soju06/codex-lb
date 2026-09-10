## 1. Run-state sidecar

- [x] 1.1 Add `SqliteRunState` plus `sqlite_runstate_path`,
  `read_sqlite_runstate`, and `write_sqlite_runstate` to `app/db/sqlite_utils.py`.
- [x] 1.2 Write the sidecar atomically (temp file + `os.replace`) and remove
  it when the write fails, so a stale `clean` can never survive.
- [x] 1.3 Read unrecognized or unreadable content as unknown.
- [x] 1.4 Fence a `clean` record to the database file's device, inode, size,
  mtime, and ctime, so a timestamp-preserving restore cannot inherit the
  previous file's clean record.
- [x] 1.5 Read content that cannot be decoded as UTF-8 as unknown rather than
  letting the error abort startup.
- [x] 1.6 Fsync the record's contents before the rename and the directory
  entry after it, so a power loss cannot lose a `running` transition.
- [x] 1.7 Treat every directory-sync failure as a failed write, including a
  directory that cannot be opened, and skip the sync only on a platform that
  offers no directory handle at all (Windows), decided by platform rather
  than by the error the open reports.
- [x] 1.8 Add a persistent `<db>.runstate.lock` SQLite sentinel transaction
  that fences one process onto a file-backed database and releases on process
  death.
- [x] 1.9 Create sidecar temporary files with `tempfile.mkstemp` rather than a
  predictable PID-derived pathname.
- [x] 1.10 Treat recursive or malformed JSON and a clean record with a null
  identity as unknown.

## 2. Startup

- [x] 2.1 Skip the integrity scan in `init_db()` only when the sidecar
  records `clean`.
- [x] 2.2 Record `running` on every startup, including when the check mode is
  `off`, so re-enabling the check cannot trust a state this build never wrote.
- [x] 2.3 Log the scan before it starts with path, mode, and file size, and
  log its elapsed duration on success.
- [x] 2.4 Acquire the lifetime lock before reading the sidecar and fail startup
  closed when another process already owns it.
- [x] 2.5 Read the prior record, persist `running`, and require a successful
  running transition before a clean skip; failed transitions force the scan.
- [x] 2.6 Require matching non-null prior/running/current identities and
  revalidate at the final decision seam so replacement races force the scan.
- [x] 2.7 Distinguish a durably cleaned failed `running` transition from an
  unconfirmed invalidation, run the configured check first when enabled, and
  abort startup before migrations or serving when invalidation durability is
  unknown.

## 3. Shutdown

- [x] 3.1 Add `mark_sqlite_shutdown_clean()` and record the clean state only
  after `close_db()` returns, so a cancelled or failed disposal stays unclean.
- [x] 3.2 Extract `_close_db_and_record_clean_shutdown()` so the ordering is
  testable, and keep `mark_lifespan_completed()` in the unconditional
  `finally`.
- [x] 3.3 Permit the clean transition only while the lifetime lock is held and
  release that lock after the write attempt.
- [x] 3.4 Return whether the reclaimed SQLite teardown registry fully drained,
  and withhold `clean` when the bounded drain abandons work.
- [x] 3.5 Propagate final proxy persistence, detached audit/fleet control-plane,
  and scheduler leader-release outcomes into clean-state recording.

## 4. Verification

- [x] 4.1 Unit-test the sidecar round trip, the unknown-content read, and the
  write-failure path that clears a stale `clean`.
- [x] 4.2 Unit-test that `init_db()` skips the scan after a clean shutdown and
  runs it for a missing sidecar, a `running` sidecar, and a disabled check.
- [x] 4.3 Unit-test that a failed integrity check leaves the state unclean.
- [x] 4.4 Unit-test the invalid-UTF-8 sidecar, the timestamp-preserving
  restore, that both syncs happen on a write, that a failed directory sync
  fails the write closed, that an unopenable directory fails closed, and that
  a platform without directory handles skips the sync. Drive the sync-failure
  path through a stubbed handle so it runs on every platform.
- [x] 4.5 Unit-test that a raised or cancelled `close_db()` does not record a
  clean shutdown.
- [x] 4.6 Run Ruff check/format and `ty`.
- [x] 4.7 Test lock contention, clean-transition lock release, startup fail
  closed on contention, and the symlink-safe temporary-file path.
- [x] 4.8 Test failed running transitions, failed-check retries, all
  replacement windows, null identities, recursive JSON, and the second
  directory fsync after post-replace cleanup.
- [x] 4.9 Add subprocess proof for lock contention, release after a clean child
  exit, and release after child process death.
- [x] 4.10 Add regression coverage for unconfirmed failed-marker invalidation,
  abandoned SQLite teardown, and each database-owning shutdown drain suppressing
  the clean marker.
- [x] 4.11 Add regression coverage for failed HTTP bridge durable-session
  marking or closure suppressing the clean marker.
- [x] 4.12 Treat a failed or timed-out HTTP bridge background cleanup drain as
  an incomplete closure and suppress the clean marker.
