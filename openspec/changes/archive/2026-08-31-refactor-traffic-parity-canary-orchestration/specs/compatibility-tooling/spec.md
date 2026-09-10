## MODIFIED Requirements

### Requirement: Version-aware traffic canary runs without false success

The canary runner MUST execute its configured fast live suite when the detected
Codex version differs from the last successful version or the last successful
run is at least the configured weekly interval old. It MUST serialize runs
with an exclusive lock, invoke an argv without a shell, use a new approved
scratch run directory, and atomically advance state only after exit 0. The
configured argv MUST delegate suite orchestration, gate evaluation, cleanup,
privacy scanning, and result generation to a repository-owned testable module;
host-local configuration MUST supply explicit paths rather than embed a second
suite implementation. Missing configuration, overlap, timeout, command
failure, incomplete cleanup, or failed privacy checks MUST NOT advance the
successful version or timestamp.

#### Scenario: Codex version changes

- **GIVEN** the last successful state records Codex 0.150.1
- **AND** the installed client reports 0.151.0
- **WHEN** the daily checker runs
- **THEN** it launches the fast live suite with trigger `version_changed`
- **AND** records 0.151.0 only if the suite succeeds

#### Scenario: Weekly interval elapses

- **GIVEN** the Codex version is unchanged
- **AND** the configured interval has elapsed since the last success
- **WHEN** the checker runs
- **THEN** it launches the suite with trigger `interval_elapsed`

#### Scenario: Another canary owns the lock

- **WHEN** a scheduled checker overlaps an active canary
- **THEN** the new checker exits without starting a second suite
- **AND** it does not alter successful state

#### Scenario: Host configuration invokes repository orchestration

- **GIVEN** the host scheduler decides a canary is due
- **WHEN** it invokes the configured command
- **THEN** the command uses explicit repository, runner, auth, and approved
  scratch paths
- **AND** repository-owned code performs validation, cleanup, scanning, and
  result generation

#### Scenario: Suite command fails after creating sensitive state

- **GIVEN** a controlled runner created an isolated database, key, or log
- **WHEN** a later suite step fails
- **THEN** enumerated sensitive subtrees are removed before the command exits
- **AND** no successful result or scheduler state is written

#### Scenario: Fast canary succeeds

- **WHEN** raw HTTP/2 and controlled failure gates pass and cleanup completes
- **THEN** the run is labelled `fast_canary`
- **AND** it is not reported as a full TLS/composite attestation
