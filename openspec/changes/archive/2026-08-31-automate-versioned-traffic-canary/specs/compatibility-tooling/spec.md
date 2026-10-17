## MODIFIED Requirements

### Requirement: Composite traffic parity gate fails closed

The traffic toolkit MUST provide a single offline gate that combines same-run
B/C semantic comparison, independently sampled A′/A/C TLS comparison,
controlled A′/A/C raw HTTP/2 comparison, and an optionally required controlled
failure matrix. It MUST require explicit non-zero coverage for every configured
semantic and TLS transport and every configured failure scenario. Strict
success MUST require zero semantic hard mismatches, matching required TLS
cohorts, every stable direct-repeatability and routed HTTP/2 dimension to be
observed and matching, and every required failure scenario to satisfy its
end-to-end policy. Missing, malformed, undersampled, or incomplete required
evidence MUST fail the gate.

#### Scenario: Complete evidence passes

- **GIVEN** required SSE and WebSocket semantic turns match B/C
- **AND** required HTTP JSON, SSE, and WebSocket TLS cohorts match
- **AND** all A′/A and A/C raw HTTP/2 dimensions match
- **AND** every required controlled failure scenario satisfies its A/B policy
- **WHEN** the composite gate runs in strict mode
- **THEN** it exits successfully and reports every section as passed

#### Scenario: A transport is absent

- **GIVEN** semantic comparison has no WebSocket turn on one required leg
- **WHEN** the composite gate runs
- **THEN** semantic coverage fails even if all observed turns match

#### Scenario: Direct HTTP/2 repeatability fails

- **GIVEN** A/C match but a stable A′/A HTTP/2 dimension differs
- **WHEN** the composite gate runs
- **THEN** the aggregate result fails rather than accepting an unstable direct
  baseline

#### Scenario: Required failure scenario is absent

- **GIVEN** the composite policy requires HTTP 429 behavior
- **AND** no HTTP 429 comparison result is supplied
- **WHEN** the composite gate runs
- **THEN** the failure section and aggregate result fail closed

## ADDED Requirements

### Requirement: Controlled failure matrix gates client-visible recovery

The traffic toolkit MUST project each configured controlled scenario onto
bounded attempt counts, A/B outcome classes, HTTP statuses, retry hints,
completion state, per-attempt relations, and final relation. A scenario MUST
require equal non-zero A/B attempt counts, compatible A/B turns, a compatible
final outcome, and its versioned expected profile. Scenarios whose contract is
successful or transparent HTTP rejection MUST also require the ordinary strict
B/C semantic gate. Expected incomplete transport scenarios MAY retain a strict
B/C mismatch when their explicit A/B recovery profile matches; the report MUST
show that distinction rather than call the raw transport identical.

#### Scenario: Transparent HTTP rejection passes

- **GIVEN** direct and through-LB Codex each make one HTTP 429 attempt
- **AND** both observe status 429 with the same bounded retry hint
- **AND** B/C strict semantics pass
- **WHEN** the failure matrix is evaluated
- **THEN** the HTTP 429 scenario passes

#### Scenario: WebSocket recovery matches end to end

- **GIVEN** direct and through-LB Codex make the expected equal attempt count
- **AND** their attempt relations and final successful outcome match the
  versioned WebSocket recovery profile
- **WHEN** an upstream framing translation remains visible on B/C
- **THEN** the scenario passes its client-visible gate
- **AND** the report does not relabel B/C framing as exact

#### Scenario: Attempt count drifts

- **GIVEN** direct Codex makes three attempts and through-LB Codex makes two
- **WHEN** the failure matrix is evaluated
- **THEN** the scenario fails even if both final outcomes are successful

### Requirement: Version-aware traffic canary runs without false success

The canary runner MUST execute its configured fast live suite when the detected
Codex version differs from the last successful version or the last successful
run is at least the configured weekly interval old. It MUST serialize runs with
an exclusive lock, invoke an argv without a shell, use a new approved scratch
run directory, and atomically advance state only after exit 0. Missing
configuration, overlap, timeout, command failure, incomplete cleanup, or failed
privacy checks MUST NOT advance the successful version or timestamp.

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

#### Scenario: Fast canary succeeds

- **WHEN** raw HTTP/2 and controlled failure gates pass and cleanup completes
- **THEN** the run is labelled `fast_canary`
- **AND** it is not reported as a full TLS/composite attestation
