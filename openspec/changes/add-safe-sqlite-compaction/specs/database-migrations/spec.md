## ADDED Requirements

### Requirement: SQLite compaction plans without mutation

The owned database CLI MUST provide a dry-run compaction mode for file-backed
SQLite that reports the source path and size, page size/count, freelist pages
and reclaimable bytes, autovacuum mode, and conservative free-space
requirement. Dry-run MUST NOT run `VACUUM`, create a backup or temporary
database, checkpoint WAL, or modify the source. It MUST use an immutable
read-only connection, reject a non-empty WAL or rollback journal with a
non-zero hot-journal header whose committed state cannot be included without
recovery or sidecar mutation, report the source size from the verified
snapshot, and fail if the database or those sidecars change during inspection.

#### Scenario: Dry-run reports reclaimable storage

- **GIVEN** a file-backed SQLite database with freelist pages
- **WHEN** the operator runs compaction dry-run
- **THEN** the CLI reports page and byte estimates
- **AND** the source and its sidecars remain unchanged

### Requirement: SQLite compaction verifies before replacement

Executing compaction MUST require explicit acknowledgement that all application
replicas are stopped. The tool MUST reject concurrent compaction, a busy WAL
checkpoint, free disk below two checkpointed logical source-file sizes plus its fixed reserve,
failed source or output integrity, schema identity mismatch, or an observed
external write before replacing the source.
It MUST create and verify the compacted database in the source directory,
enable incremental autovacuum on the output, close every non-lock-owning
connection before replacement, preserve the original as a unique timestamped
backup, and fsync the replacement and directory. The complete `VACUUM INTO`
creation window MUST run under a restrictive `077` umask. The temporary copy
MUST retain owner-write permission through all output mutations, then receive
the source mode, uid, and gid before integrity verification and installation.
Before the final source validation, it MUST acquire an exclusive SQLite write
lock and hold it through sidecar handling and replacement, so a concurrent
writer cannot commit into the validation-and-replacement window. The source
and replacement lock-owning connections, together with lock-safe fsync
descriptors opened before their respective locks, MAY remain open through
their corresponding durability checks.
When enabling incremental autovacuum, it MUST recheck free space for the
pointer-map-expanded output before the output-only `VACUUM`, and it MUST hold
an exclusive lock on the replacement inode through its file and directory
durability checks.
That auto-vacuum check MUST also validate the writable filesystem SQLite will
use for its temporary files and combine simultaneous output and temporary-file
allocations when they share one filesystem.
It MUST reject replacement of the source path with a different inode while
compaction is running, reserve a backup name only when its main and sidecar
paths are unused, and restore the original database and sidecars if
installation is interrupted.
The original MUST be durably linked at the backup path before the verified
temporary database atomically replaces the live source path, so the live path
is never absent between two rename operations. The temporary database MUST be
created inside an atomically allocated, owner-only directory under the source
directory. Execution MUST reject platforms where directory-entry durability
cannot be enforced.

#### Scenario: Verified compaction preserves data and rollback source

- **GIVEN** the application is stopped and the SQLite database is healthy
- **WHEN** the operator confirms and executes compaction
- **THEN** every committed row and Alembic revision remains present
- **AND** the replacement passes quick integrity checking
- **AND** the replacement uses incremental autovacuum
- **AND** the original database remains at the reported backup path

#### Scenario: Unsafe precondition leaves source untouched

- **WHEN** free space is insufficient, WAL checkpoint is busy, source
  integrity fails, or another compaction holds the lock
- **THEN** compaction fails before source replacement
- **AND** the source path still identifies the original database

#### Scenario: Replacement failure restores the original

- **GIVEN** a verified temporary database and preserved original backup
- **WHEN** moving the temporary database into the source path fails
- **THEN** the tool restores the original source before returning failure

#### Scenario: Interrupted replacement restores sidecars

- **GIVEN** one or more stopped-instance sidecars were moved beside the backup
- **WHEN** replacement is interrupted
- **THEN** the original source and sidecars are restored before the interruption propagates

### Requirement: Compaction preserves stopped-instance sidecars

After a successful zero-busy WAL checkpoint, compaction MUST reject a non-empty
WAL and MUST preserve any remaining WAL or SHM sidecar under backup-derived
names during replacement. It MUST NOT silently delete sidecar files.

#### Scenario: Non-empty WAL blocks replacement

- **GIVEN** WAL still contains bytes after the checkpoint
- **WHEN** compaction executes
- **THEN** replacement is rejected and the source remains untouched
