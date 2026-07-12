# dev-workflow Specification

## Purpose

Define and implement a machine-readable contract that encodes the
recurring codex-lb development tasks (`feature`, `bugfix`,
`release-beta`, `release-stable`, `sync-upstream`) as ordered phases
with explicit approval gates, expose it as a `/process` Claude Code
skill that walks the user through those phases, and gate PRs with a
GitHub Action that enforces the same rules from the CI side.

## Requirements
### Requirement: Process contracts

The system SHALL provide five machine-readable process contracts at
`openspec/process/contracts/{feature,bugfix,release-beta,release-stable,sync-upstream}.yaml`.
Each contract SHALL validate against `openspec/process/contracts/schema.json`.
Each contract SHALL declare: `trigger`, ordered `phases` (each with `name`,
`description`, `irreversible` boolean, `stop_signals`, and
`expected_artifacts`), and an `interruption_commands` list drawn from
`stop`, `rollback`, `explain`, `skip`.

#### Scenario: All five contracts load and validate

- **WHEN** `uv run python openspec/process/scripts/validate_contracts.py`
  is invoked from the repository root
- **THEN** the script exits with code 0
- **AND** each of the five required contracts validates against the schema

#### Scenario: Invalid contract is rejected

- **WHEN** any contract file fails schema validation
- **THEN** the script exits non-zero
- **AND** the offending file path and validation error are printed to stderr

### Requirement: /process skill

The system SHALL expose a Claude Code skill at
`.claude/skills/process/SKILL.md` whose `description` triggers on phrases
matching `/process <task-type>` for any of the five task types. The skill
SHALL load the matching contract and SHALL pause for explicit user
approval before executing any phase marked `irreversible: true`. The skill
SHALL write a per-task history entry under
`openspec/changes/<slug>/notes.md` (for `feature` and `bugfix`) or append
to `openspec/process/release-log.md` (for release tasks). The skill SHALL
honor `stop`, `rollback`, `explain`, and `skip` commands at any phase
boundary.

#### Scenario: Irreversible phase requires explicit approval

- **WHEN** the user invokes `/process feature` and Claude reaches the
  `merge` phase (which has `irreversible: true`)
- **THEN** Claude SHALL print a dry-run summary and SHALL NOT call
  `gh pr merge` until the user replies with the exact confirmation phrase
  declared in the contract

#### Scenario: Stop command halts immediately

- **WHEN** the user types `stop` during any phase
- **THEN** Claude SHALL halt at the current phase
- **AND** Claude SHALL NOT advance to the next phase without a fresh
  invocation of `/process`

### Requirement: process-map.md

The system SHALL publish `openspec/process/process-map.md` containing a
mermaid diagram that depicts every contract's phase flow and a short prose
description of each task type. The file SHALL be linked from `CLAUDE.md`
and SHALL cross-link to each contract YAML.

#### Scenario: Cheat sheet is discoverable from CLAUDE.md

- **WHEN** a new Claude Code session loads `CLAUDE.md`
- **THEN** `CLAUDE.md` SHALL contain a pointer to
  `openspec/process/process-map.md`

### Requirement: process-check GitHub Action

The system SHALL provide `.github/workflows/process-check.yml` that runs on
every PR. The workflow SHALL validate every contract against the JSON
Schema. The workflow SHALL fail the PR if any contract file is missing,
malformed, or fails validation. The workflow SHALL be triggered from the
existing `ci.yml` so that PRs do not need to opt in.

#### Scenario: PR with bad contract fails process-check

- **WHEN** a PR modifies `openspec/process/contracts/feature.yaml` so that
  it no longer validates against the schema
- **THEN** the `process-check` job exits non-zero
- **AND** the PR's required status check is red

### Requirement: OpenSpec gate for behavior changes

The system SHALL treat changes to behavior, API, schema, CLI,
dashboard-visible behavior, or proxy-routing as OpenSpec-gated, per the
existing CONTRIBUTING.md rule. The `/process feature` and `/process bugfix`
contracts SHALL include a `verify-openspec-change` phase that runs
`openspec validate --change <slug>` before any commit is pushed.

#### Scenario: Feature without OpenSpec change is rejected

- **WHEN** the user invokes `/process feature` for a behavior-changing
  feature
- **THEN** the `verify-openspec-change` phase SHALL fail if no
  `openspec/changes/<slug>/` folder exists
- **AND** the skill SHALL halt with a clear remediation message

