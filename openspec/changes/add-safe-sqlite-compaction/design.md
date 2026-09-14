# Design: Add safe SQLite compaction

## Context

The current recovery CLI can rebuild a corrupt database, and migration backup
uses SQLite's online backup API. Neither provides a normal maintenance path
that reclaims freelist pages while preserving a byte-for-byte rollback source.

## Decisions

### D1. Dry-run is read-only

Dry-run resolves the file-backed SQLite path and reports source size, page
size/count, freelist pages and bytes, current autovacuum mode, and conservative
free-space requirement. It uses immutable read-only access, does not run
integrity check or create files, and rejects non-empty WAL/hot-journal state.

### D2. Execution requires explicit maintenance acknowledgement

`--execute` requires `--confirm-stopped`. The tool also acquires an exclusive
compaction lock file, performs a zero-busy WAL checkpoint, and rejects an
observed external `data_version` change. After output verification, an
exclusive SQLite transaction blocks writers through the final validation,
sidecar handling, and replacement window. These checks supplement but do not
replace the operator's responsibility to stop every replica.

### D3. Build and verify before replacement

Use `VACUUM INTO` to create a temporary database inside an atomically allocated
owner-only directory beneath the source directory. Configure the output for
incremental autovacuum, running a second output-only `VACUUM` only
when the copied database did not inherit that mode. Require `quick_check=ok`
and matching SQLite application/user versions plus Alembic revision rows. The
free-space gate first accounts for pending WAL bytes, then rechecks the
checkpointed logical source size before `VACUUM INTO`; it reserves two source
sizes for the output and its second VACUUM scratch space. Before enabling
incremental autovacuum, it rechecks space against the pointer-map-expanded
output size on both the source filesystem and SQLite's selected temporary-file
filesystem; when both paths share one filesystem, it combines their
simultaneous allocation requirements. Creation runs under `umask 077`; source
permissions are restored only after the output-only mutations complete and
before output integrity verification.

### D4. Preserve rollback source

Close every SQLite connection except the source and replacement connections
that own the required exclusive locks. Hard-link the source to a unique
`pre-compact` backup, fsync the directory through descriptors opened before
their SQLite locks, then atomically replace the source path with the verified
temporary file. Preserve source mode/uid/gid and fsync the file and directory.
If installation or its durability
checks fail, or is interrupted, restore the original. Preserve any
stopped-instance sidecar files under backup names rather than deleting them;
choose the backup name only when its base and sidecar paths are all unused.
Record the source inode before compaction and reject a path replacement before
installing the output. Before installation, acquire the replacement inode's
exclusive SQLite lock and retain it through file and directory durability so a
writer cannot enter between rename and success or rollback. Reject execution
on platforms where directory-entry durability cannot be enforced.

## Risks / Trade-offs

- Compaction performs at least one full read/write of the database and can take
  minutes on slow storage.
- The stopped-service acknowledgement is necessary because generic process
  discovery cannot prove that every container or remote replica is stopped.
- Backups consume the original file size until the operator validates and
  removes them separately.

## Rollback

Stop the service, move the compacted source aside, and restore the reported
`pre-compact` backup and its preserved sidecars. The tool never removes that
backup automatically.
