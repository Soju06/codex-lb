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

