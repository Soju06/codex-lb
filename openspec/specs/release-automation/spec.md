# release-automation Specification

## Purpose
TBD - created by archiving change cleanup-superseded-beta-release-prs. Update Purpose after archive.
## Requirements
### Requirement: Superseded beta release PR cleanup

The beta release sync workflow SHALL close open automation-managed beta release PRs that no longer match the current beta release branch.

#### Scenario: Current train supersedes an older beta PR

- **GIVEN** the sync workflow prepares beta branch `release/beta-1.20.0-beta.1`
- **AND** an open PR from `release/beta-1.19.1-beta.1` contains the beta automation sentinel text
- **WHEN** the current beta PR is opened or updated
- **THEN** the workflow closes the older beta PR with a superseded explanation
- **AND** deletes the older repository-owned beta branch

#### Scenario: Current beta PR is preserved

- **GIVEN** the sync workflow prepares beta branch `release/beta-1.20.0-beta.1`
- **AND** an open PR from `release/beta-1.20.0-beta.1` exists
- **WHEN** cleanup evaluates open beta PRs
- **THEN** the workflow does not close or delete the current beta PR

#### Scenario: Manual and protected beta PRs are preserved

- **GIVEN** an open PR uses a `release/beta-*` head branch
- **AND** the PR lacks the beta automation sentinel text or carries a protected operator label
- **WHEN** cleanup evaluates open beta PRs
- **THEN** the workflow does not close or delete that PR

### Requirement: Beta release PR creation parity

The beta release sync workflow SHALL only create or update an automation-managed beta release PR when the latest beta tag does not cover all releasable Conventional Commit changes on `main`.

#### Scenario: Latest beta tag is followed only by non-releasable CI changes

- **GIVEN** the latest beta tag is `v1.20.0-beta.2`
- **AND** `main` contains only `ci:`, `chore:`, `docs:`, `test:`, or other non-releasable commits after that tag
- **WHEN** the beta release sync workflow evaluates the next beta release
- **THEN** the workflow does not create or update a `v1.20.0-beta.3` PR
- **AND** the workflow reports that the latest beta tag already covers all releasable commits

#### Scenario: Latest beta tag is followed by a releasable change

- **GIVEN** the latest beta tag is `v1.20.0-beta.2`
- **AND** `main` contains a releasable Conventional Commit after that tag, such as `feat:`, `fix:`, `perf:`, `deps:`, `revert:`, or a breaking-change commit
- **WHEN** the beta release sync workflow evaluates the next beta release
- **THEN** the workflow creates or updates the next beta release PR
- **AND** the beta release PR targets `v1.20.0-beta.3`

### Requirement: Beta release PR changelog

The beta release sync workflow SHALL include a generated changelog section in the automation-managed beta release PR body.

#### Scenario: Beta PR contains releasable changes since the previous beta tag

- **GIVEN** the latest beta tag is `v1.20.0-beta.2`
- **AND** `main` contains a releasable Conventional Commit after that tag
- **WHEN** the beta release sync workflow opens or updates the next beta release PR
- **THEN** the PR body includes a `Changes since v1.20.0-beta.2` section
- **AND** the section lists the releasable Conventional Commit subjects included in that beta PR

