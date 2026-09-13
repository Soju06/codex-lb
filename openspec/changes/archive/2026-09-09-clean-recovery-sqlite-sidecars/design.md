## Context

Recovery exports the source and imports a dump into a new output before an
optional replacement. SQLite sidecars are separate filesystem entries and are
not part of the dump, so they must be removed at each replacement boundary.

## Decisions

- Remove `-wal`, `-shm`, and `-journal` by exact path.
- Remove `-mj*` master journals by globbing an escaped database basename.
- Reject identical source/output paths and any source/output pair where either
  path is the other's fixed sidecar or `-mj*` master journal before cleanup or
  output creation.
- Remove pre-existing output sidecars before opening the recovery lock so stale
  WAL/journal state cannot attach during import. Hold `BEGIN EXCLUSIVE` on the
  source before exporting it, generate the dump from that lock-holding
  connection, and retain the transaction across final output import. Release
  and close that connection before final output/source sidecar cleanup or
  either database rename because Windows rejects filesystem mutation with an
  open SQLite handle. Repeat source cleanup after moving the source to its
  backup so sidecars recreated around that move are removed before the
  recovered output is installed. The exclusive transaction remains the
  pre-replacement race/ownership fence: active writers fail closed while the
  snapshot is exported and the replacement is prepared, and a lock failure
  aborts before any rename. Closing the probe necessarily leaves a bounded
  post-probe window before the cleanup and renames; the operator must keep
  external writers quiescent throughout that window.
- Fail recovery rather than install an ambiguous replacement when pre-move
  sidecar removal reports an error. If the repeat source cleanup after the
  source move fails, restore the backup to the source path before reporting
  the cleanup error; leave the recovered output and any partial cleanup in
  place as operator-recovery artifacts rather than installing it as the live
  source.
- Treat the two filesystem renames as a small transaction: if installing the
  output fails after the source moved to its backup, restore the backup to the
  source path and report the original failure. If restoration also fails,
  include both errors so the operator can recover the preserved backup.

## Proof seam

Recovery tests hold a source WAL open across dump creation, seed output
sidecars, and verify the installed database and both sidecar sets. A snapshot
boundary test attempts a write while the lock-holding connection exports the
source, closes that external writer before lock release, and verifies that a
fresh connection writes to the installed database in both output-only and
replacement flows. A filesystem seam tracks
every sidecar unlink and database rename, asserting that every tracked recovery
connection is closed first; it also recreates a source sidecar around the
source move to prove the repeat cleanup. Partial cleanup, busy-source, and
second-rename failures prove the replacement fails closed, and a wildcard
filename test proves unrelated master journals remain untouched. A rollback
seam injects a repeat-cleanup failure and verifies source restoration; the lock
seam also verifies the handle closes when rollback itself raises.

## Dependencies

The implementation and proof stand on the beta.4 base without the startup
run-state candidate. The hosted Contributors attribution check is a merge
dependency on #1902, the sole carrier of contributor metadata; this change
must not duplicate `.all-contributorsrc` or README edits.
