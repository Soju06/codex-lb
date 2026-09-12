# database-migrations Delta

## ADDED Requirements

### Requirement: SQLite recovery MUST fence replacement sidecars

Before any sidecar cleanup or output write, recovery MUST reject source/output
paths that are identical or overlap either path's fixed SQLite sidecars or
master-journal namespace.

When recovery writes or installs a file-backed SQLite replacement, it MUST
remove the target's `-wal`, `-shm`, `-journal`, and master-journal sidecars
before and after dump import. For both output-only and `--replace` flows,
pre-existing output sidecars MUST be removed before opening the recovery lock;
recovery MUST then acquire an exclusive SQLite transaction on the source before
exporting the source dump, generate that dump from the lock-holding connection,
and retain the transaction through final output import. Once that transaction
closes, recovery MUST perform final output/source sidecar cleanup before either
database rename. It MUST remove source sidecars before moving the source to its
corrupt backup and repeat source cleanup after that move, before installing the
output. Master-journal matching MUST treat the database basename literally.
Recovery MUST close every recovery-opened SQLite connection before each sidecar
unlink or database rename. The operator MUST keep external writers quiescent
from lock release through completion of both renames; this is the bounded
post-probe window required by platforms that reject filesystem mutation with
open SQLite handles. If an active connection prevents the lock, or any
pre-move sidecar cleanup fails, recovery MUST fail without moving the source or
installing the output. If the repeat source cleanup after the source move
fails, recovery MUST restore the source from its corrupt backup before
reporting the cleanup failure; the recovered output MUST NOT be installed as
the live source, though the output and any partially cleaned sidecars MAY
remain for operator recovery.

#### Scenario: A stale source WAL cannot attach to the replacement

- **GIVEN** recovery is replacing a file-backed SQLite database
- **AND** source WAL/shared-memory sidecars contain rows that are absent from
  the exported dump
- **AND** external writers remain quiescent after the recovery lock closes
  until both replacement renames complete
- **WHEN** recovery moves the source aside and installs the output
- **THEN** source and output SQLite sidecars MUST be absent
- **AND** reopening the installed database MUST not apply stale WAL rows

#### Scenario: An active writer cannot cross the fenced snapshot boundary

- **GIVEN** a source connection is open while recovery is replacing the database
- **AND** the external writer closes its connection before recovery releases
  the lock and starts replacement renames
- **WHEN** that connection attempts a write while recovery exports the source
  from the lock-holding transaction
- **THEN** the write MUST fail with the source's exclusive recovery lock held
- **AND** a fresh connection MUST be able to write to the installed database

#### Scenario: Recovery closes handles before Windows renames

- **GIVEN** recovery has prepared an output replacement
- **WHEN** it moves the source to its corrupt backup and the output into place
- **THEN** every recovery-opened SQLite connection MUST already be closed before each rename
- **AND** both file mutations MUST succeed on a platform with exclusive rename handles

#### Scenario: A busy source fails closed before replacement

- **GIVEN** another process already holds a conflicting SQLite write lock
- **WHEN** recovery cannot acquire its exclusive source lock
- **THEN** recovery MUST fail
- **AND** the source MUST remain at its original path
- **AND** no replacement MUST be installed

#### Scenario: Partial sidecar cleanup fails closed

- **GIVEN** one target sidecar cannot be removed while other sidecars can be removed
- **WHEN** recovery prepares a replacement
- **THEN** recovery MUST fail before moving the source or installing the output
- **AND** the source MUST remain at its original path

#### Scenario: Post-move sidecar cleanup restores the source

- **GIVEN** recovery has moved the source to its corrupt backup
- **AND** the repeat source sidecar cleanup fails
- **WHEN** recovery handles the cleanup error
- **THEN** the corrupt backup MUST be restored to the original source path
- **AND** the recovered output MUST remain uninstalled as the live source
- **AND** recovery MUST report the cleanup failure

#### Scenario: Output installation failure restores the source

- **GIVEN** the source has moved to its corrupt backup
- **AND** moving the recovered output into the source path fails
- **WHEN** recovery handles the replacement error
- **THEN** recovery MUST restore the corrupt backup to the original source path
- **AND** recovery MUST report the installation failure

#### Scenario: Wildcard names do not broaden cleanup

- **GIVEN** the database basename contains a glob metacharacter
- **AND** an unrelated database has a matching-looking master journal
- **WHEN** recovery cleans the target sidecars
- **THEN** the target journal MUST be removed
- **AND** the unrelated journal MUST remain

#### Scenario: Source and output sidecar namespaces cannot overlap

- **GIVEN** the source or output path is a fixed SQLite sidecar or master
  journal of the other path
- **WHEN** recovery is invoked in either replace or non-replace mode
- **THEN** recovery MUST fail before deleting sidecars, writing output, or
  moving the source
