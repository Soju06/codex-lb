# database-backends Delta

## ADDED Requirements

### Requirement: The SQLite startup integrity check is skipped after a recorded clean shutdown

The startup integrity check reads every page of the SQLite file and the
listener MUST NOT bind until it returns, so its cost grows with the store.
Because SQLite is already consistent after a clean close, the system MUST
record how each process left the store and MUST run the startup check only
when the previous process did not record a clean shutdown.

The run state MUST be persisted in a sidecar next to the database file. The
system MUST record `running` during startup and MUST record `clean` only
after the database engines are disposed during an orderly shutdown. The
`clean` record MUST NOT be reachable from a crash, a signal-killed process,
or a failed startup.

Startup MUST read the prior run-state record before mutating it, then MUST
persist `running` before deciding whether a prior `clean` record permits
skipping the integrity check. If the `running` transition cannot be recorded,
startup MUST run the configured check when enabled and MUST NOT trust the
prior `clean` record. When the failed transition has durably removed the
untrusted sidecar, startup MAY continue after the configured check; when
removal or its directory-sync durability cannot be confirmed, the write MUST
report a distinct durability failure and startup MUST run the configured check
when enabled, then abort before migrations or serving. A failed startup MUST
leave a durable `running` marker where the sidecar can be written.

Before reading the run-state sidecar, startup MUST acquire an exclusive
transaction on a persistent `<db>.runstate.lock` SQLite sentinel. Startup MUST
fail closed when that lifetime lock cannot be acquired, rather than trusting a
`clean` marker while another process may be using the database. The process
MUST hold the lock through its database lifetime and MUST release it only after
the `clean` transition has been attempted. SQLite's transaction semantics MUST
release the lock when the process exits unexpectedly. A `clean` transition MUST
be attempted only while the corresponding lifetime lock is held.

Every state other than a recorded `clean` MUST run the check. A missing
sidecar MUST read as unknown rather than clean, so a first run and an upgrade
from a build that never wrote one both still scan. Sidecar content that cannot be read, cannot be
decoded, or is not recognized MUST also read as unknown, and MUST NOT
propagate an error that aborts startup. A sidecar write that fails MUST
remove the temporary and target entries rather than leave a stale `clean`
behind, and MUST directory-sync that removal. If either removal or its
directory sync cannot be confirmed, the write MUST fail closed to its caller.

The run state MUST be recorded even when the check mode is `off`, so
re-enabling the check cannot trust a state the disabled build never
maintained.

A `clean` record MUST be fenced to the database file it describes. The
recorded state MUST capture enough of the file's identity to detect that it
was replaced, including its device and inode, not only its size and
modification time: a restore that preserves timestamps (`tar -x`, `cp -p`,
`rsync -a`) can reproduce both. A `clean` record MUST read as unknown once
any captured attribute no longer matches. The fence applies only to `clean`;
a `running` record stays readable while the process writes to the store. A
clean identity, the newly persisted running identity, and the current database
identity MUST all be non-null and equal before a clean skip. Startup MUST
revalidate the running identity at the final decision seam, so replacement in
any window forces the integrity check.

Run-state transitions MUST be durable. The system MUST sync both the record's
contents and the directory entry that names it, so a power loss cannot retain
an earlier `clean` record while losing the `running` transition that replaced
it. Every directory-sync failure MUST be treated as a failed write, including
a directory that cannot be opened, so storage that cannot confirm durability
leaves no trusted record. The one exception is a platform that offers no
directory handle at all, where the sync MUST be skipped and reported as
success, because rename durability there is the platform's guarantee and
failing closed would prevent those deployments from ever recording a clean
shutdown. That exception MUST be decided by platform rather than by the error
the open reports, because Windows refuses a directory handle with the same
`EACCES` an ordinary permission denial uses. The file fence cannot substitute for this: in WAL mode the main database
file can keep its size and modification time across a long run, so a lost
transition would leave a `clean` record that still matches.

If the directory sync fails after a sidecar replacement, cleanup MUST remove
the temporary and target entries and MUST sync the parent directory again so
the removal itself is durable. A failure of that second sync MUST remain
fail-closed.

Each run-state write MUST create its temporary file with an exclusive,
unpredictable name in the target directory before writing, syncing, and
atomically replacing the sidecar. A predictable temporary pathname MUST NOT be
opened for writing.

Recording `clean` MUST NOT be reachable unless the database engines actually
finished disposing, all reclaimed SQLite teardown work finished within the
bounded shutdown drain, and every database-owning shutdown drain completed.
The database-owning drains include HTTP bridge durable-session marking and
closure, scheduler leader-release, final proxy persistence, and detached
audit/fleet control-plane work. A cancelled or failed disposal, or a drain
that abandons pending work at its deadline, MUST leave the run state unclean.

The configured check mode (`quick`, `full`, `off`) keeps its meaning: this
requirement governs only whether the selected mode runs on a given startup.

#### Scenario: A clean shutdown skips the next scan

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the application starts with the check mode enabled
- **THEN** no integrity check runs
- **AND** the sidecar is updated to record that a process is running

#### Scenario: An unrecordable running transition cannot skip the check

- **GIVEN** a SQLite sidecar records `clean`
- **AND** writing the current `running` transition fails
- **WHEN** startup reaches the integrity-check decision
- **THEN** the configured check runs
- **AND** startup does not trust the prior `clean` record

#### Scenario: An unconfirmed running invalidation aborts startup

- **GIVEN** a SQLite sidecar records `clean`
- **AND** replacing the sidecar fails and removal or its directory sync cannot
  be confirmed
- **WHEN** startup reaches the integrity-check decision
- **THEN** the configured check runs when enabled
- **AND** startup aborts before migrations or serving
- **AND** the prior `clean` record is never trusted

#### Scenario: A replacement around startup fencing still scans

- **GIVEN** a SQLite sidecar records `clean` for database identity A
- **WHEN** the database is replaced with identity B before or after the
  `running` transition, or before the final skip decision
- **THEN** the configured integrity check runs against identity B

#### Scenario: A second process cannot trust or replace a live process's clean marker

- **GIVEN** one process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** another process starts against the same SQLite file
- **THEN** startup fails closed before it reads the clean marker
- **AND** the second process does not run migrations or serve traffic

#### Scenario: Process death releases the lifetime lock

- **GIVEN** one process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** that process exits unexpectedly
- **THEN** SQLite releases the transaction while the persistent sentinel file
  remains
- **AND** a subsequent process can acquire the lock before reading the sidecar

#### Scenario: Clean shutdown releases ownership after the marker transition

- **GIVEN** a process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** database disposal completes and shutdown records `clean`
- **THEN** the sidecar records `clean` only while that lock is held
- **AND** the lock is released after the write attempt

#### Scenario: An unfinished previous process still scans

- **GIVEN** a SQLite store whose sidecar records that a process was running
- **WHEN** the application starts with the check mode enabled
- **THEN** the configured integrity check runs

#### Scenario: A missing sidecar still scans

- **GIVEN** an existing SQLite store with no sidecar, as after an upgrade from
  a build that never wrote one
- **WHEN** the application starts with the check mode enabled
- **THEN** the configured integrity check runs

#### Scenario: A disabled check still records the run state

- **GIVEN** a check mode of `off`
- **WHEN** the application starts
- **THEN** no integrity check runs
- **AND** the sidecar records that a process is running, so a later startup
  with the check enabled does not trust the earlier clean record

#### Scenario: A restored database still scans

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the database file is replaced from a backup, leaving the sidecar
  in place, and the restore reproduces the recorded size and modification
  time
- **THEN** the clean record reads as unknown
- **AND** the configured integrity check runs against the restored file

#### Scenario: Unverifiable durability leaves no trusted record

- **GIVEN** storage whose directory sync fails
- **WHEN** the system records a run-state transition
- **THEN** the write reports failure and no sidecar remains
- **AND** the next startup runs the integrity check

#### Scenario: A directory that cannot be opened fails the write closed

- **GIVEN** a platform that supports directory handles
- **AND** a run-state directory the process cannot open
- **WHEN** the system records a run-state transition
- **THEN** the write reports failure and no sidecar remains

#### Scenario: Corrupt sidecar content does not abort startup

- **GIVEN** a sidecar whose bytes are not valid UTF-8
- **WHEN** the application starts
- **THEN** the run state reads as unknown
- **AND** the configured integrity check runs instead of startup failing

#### Scenario: A failed disposal is not recorded as clean

- **GIVEN** a shutdown in which disposing the database engines raises or is
  cancelled
- **WHEN** the lifespan teardown completes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An abandoned SQLite teardown is not recorded as clean

- **GIVEN** reclaimed SQLite teardown work remains pending when the bounded
  shutdown drain reaches its deadline
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An abandoned database-owning drain is not recorded as clean

- **GIVEN** the final proxy persistence drain, detached audit/fleet
  control-plane drain, or scheduler leader-release drain leaves database-using
  work pending at its deadline
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An incomplete HTTP bridge shutdown is not recorded as clean

- **GIVEN** HTTP bridge durable-session marking or closure fails, or its
  background cleanup drain fails, is cancelled, or times out, during shutdown
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: A failed check leaves the state unclean

- **GIVEN** a SQLite store that fails its startup integrity check
- **WHEN** startup aborts with the corruption error
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the check again

### Requirement: The SQLite startup integrity check is observable

When the startup integrity check runs, the system MUST log that it is
starting, including the database path, the check mode, and the file size, and
MUST log the elapsed duration when the check passes. A multi-minute scan MUST
NOT present as an unexplained stall with the listener unbound.

#### Scenario: A long scan is attributable

- **GIVEN** a SQLite store large enough for the check to take minutes
- **WHEN** the application starts with the check mode enabled
- **THEN** a log record precedes the scan naming the path, mode, and size
- **AND** a log record on success reports how long the scan took

#### Scenario: A skipped scan says so

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the application starts with the check mode enabled
- **THEN** a log record states that the check was skipped after a clean shutdown
