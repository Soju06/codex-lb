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

Before any controlled runner starts, that repository-owned module MUST
neutralise proactive credential refresh for the run through the isolated
credential it was given, not through process configuration: it MUST record the
isolated `auth.json` as refreshed at the current instant (rewriting only the
recorded refresh time — EVERY key the account importer accepts for it, so no
stale alias can outrank the stamp — preserving the file's restrictive mode and
never reading or logging token material), and MUST fail the run when that file
cannot be read as a JSON object or written back. Every controlled runner imports that file into its
throwaway database, and the proactive-refresh window is a fixed constant, so a
run started from a stale isolated credential would otherwise exchange a real,
single-use refresh token against the authorization host — which is a protocol
constant unaffected by the fixture upstream override — rotating the credential
into a database the suite deletes and leaving every later run with a dead
file. The suite MUST NOT set a removed `CODEX_LB_*` variable in a runner's
environment to suppress refresh.

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

#### Scenario: Controlled run cannot exchange the isolated refresh token

- **GIVEN** an isolated `auth.json` whose recorded last refresh is older than
  the fixed proactive-refresh window
- **WHEN** the suite starts a controlled run
- **THEN** it records the file as refreshed at the current instant before the
  first runner is invoked, updating every recorded-refresh key the importer
  accepts and leaving the token material and the file mode unchanged
- **AND** no runner environment carries a removed refresh-interval variable
- **AND** an `auth.json` that cannot be read as a JSON object or written back
  fails the run instead of starting it
