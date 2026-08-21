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

Every state other than a recorded `clean` MUST run the check. A missing
sidecar MUST read as unknown rather than clean, so a first run and an upgrade
from a build that never wrote one both still scan. Unreadable or unrecognized
sidecar content MUST also read as unknown. A sidecar write that fails MUST
remove the file rather than leave a stale `clean` behind.

The run state MUST be recorded even when the check mode is `off`, so
re-enabling the check cannot trust a state the disabled build never
maintained.

A `clean` record MUST be fenced to the database file it describes. The
recorded state MUST capture the file's size and modification time, and a
`clean` record MUST read as unknown once either no longer matches, so a
restored backup or a hand-swapped file cannot inherit the previous file's
clean record. The fence applies only to `clean`; a `running` record stays
readable while the process writes to the store.

The configured check mode (`quick`, `full`, `off`) keeps its meaning: this
requirement governs only whether the selected mode runs on a given startup.

#### Scenario: A clean shutdown skips the next scan

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the application starts with the check mode enabled
- **THEN** no integrity check runs
- **AND** the sidecar is updated to record that a process is running

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
  in place
- **THEN** the clean record reads as unknown
- **AND** the configured integrity check runs against the restored file

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
