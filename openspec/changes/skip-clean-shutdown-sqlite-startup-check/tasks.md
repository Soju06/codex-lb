## 1. Run-state sidecar

- [x] 1.1 Add `SqliteRunState` plus `sqlite_runstate_path`,
  `read_sqlite_runstate`, and `write_sqlite_runstate` to `app/db/sqlite_utils.py`.
- [x] 1.2 Write the sidecar atomically (temp file + `os.replace`) and remove
  it when the write fails, so a stale `clean` can never survive.
- [x] 1.3 Read unrecognized or unreadable content as unknown.
- [x] 1.4 Fence a `clean` record to the database file's size and mtime, so
  a restored backup cannot inherit the previous file's clean record.

## 2. Startup

- [x] 2.1 Skip the integrity scan in `init_db()` only when the sidecar
  records `clean`.
- [x] 2.2 Record `running` on every startup, including when the check mode is
  `off`, so re-enabling the check cannot trust a state this build never wrote.
- [x] 2.3 Log the scan before it starts with path, mode, and file size, and
  log its elapsed duration on success.

## 3. Shutdown

- [x] 3.1 Add `mark_sqlite_shutdown_clean()` and call it from the lifespan
  teardown after `close_db()`, guarded so it cannot block
  `mark_lifespan_completed()`.

## 4. Verification

- [x] 4.1 Unit-test the sidecar round trip, the unknown-content read, and the
  write-failure path that clears a stale `clean`.
- [x] 4.2 Unit-test that `init_db()` skips the scan after a clean shutdown and
  runs it for a missing sidecar, a `running` sidecar, and a disabled check.
- [x] 4.3 Unit-test that a failed integrity check leaves the state unclean.
- [x] 4.4 Run Ruff check/format and `ty`.
