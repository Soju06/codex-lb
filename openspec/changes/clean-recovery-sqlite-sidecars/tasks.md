## 1. Recovery cleanup

- [x] 1.1 Remove fixed source/output SQLite sidecars around replacement.
- [x] 1.2 Fail closed when sidecar removal cannot complete.
- [x] 1.3 Escape database basenames before matching master journals.
- [x] 1.4 Reject source/output path and sidecar namespace overlap before any
  cleanup or output write.
- [x] 1.5 Acquire an exclusive SQLite lock before source dump export, generate
  the dump from the lock-holding connection, and retain the lock through final
  import; close it before final sidecar cleanup and filesystem renames, then
  repeat source cleanup after the source move.

## 2. Verification

- [x] 2.1 Cover stale source/output WAL and journal cleanup.
- [x] 2.2 Cover literal wildcard filename matching.
- [x] 2.3 Cover a deterministic write attempt during lock-held source export
  and final import in both output-only and replacement flows.
- [x] 2.4 Cover Windows-style sidecar unlink and rename behavior with a
  tracked-handle seam, including a sidecar recreated around source move.
- [x] 2.5 Cover pre-move partial cleanup and busy-source failures.
- [x] 2.6 Cover post-move cleanup/second-rename failures and source
  restoration.
- [x] 2.7 Cover recovery-handle closure when rollback itself raises.
- [x] 2.8 Cover source/output overlap rejection in replace and non-replace mode.
- [x] 2.9 Run focused recovery tests, Ruff, formatting, `ty`, and strict OpenSpec.
