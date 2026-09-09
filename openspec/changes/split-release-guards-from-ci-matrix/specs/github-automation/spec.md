## MODIFIED Requirements

### Requirement: Automated review summaries avoid PR-body CI restarts

Repository CodeRabbit configuration MUST place generated high-level summaries
in the walkthrough comment rather than automatically updating the PR body.
Summary generation and automated review MUST remain enabled. Release evidence
edits MUST still be revalidated by the release guards workflow on the
`pull_request` `edited` event; this optimization MUST NOT skip or manufacture
successful required CI checks.

#### Scenario: Review completes after a push

- **WHEN** CodeRabbit generates its automatic high-level summary
- **THEN** its configured destination is the walkthrough comment
- **AND** summary generation does not require an automatic PR-body update

#### Scenario: Release evidence is edited

- **WHEN** a maintainer edits a release PR's validation evidence
- **THEN** the release guards workflow re-runs for that pull request
- **AND** the CI matrix workflow does not start a new run for the unchanged head

## ADDED Requirements

### Requirement: PR metadata edits do not restart the CI matrix

The CI matrix workflow (`ci.yml`) MUST NOT subscribe to the `pull_request`
`edited` event. Jobs that read pull-request metadata (title, body, head branch)
MUST live in a separate lightweight workflow with its own concurrency group
that subscribes to `opened`, `reopened`, `synchronize`, `edited`, and
`ready_for_review`. The split MUST keep every check context name unchanged.
The matrix workflow MUST NOT achieve this by keeping `edited` and skipping jobs
conditionally, because skipped check runs satisfy branch protection and a
body-only edit could then mint passing checks for a red or untested head.

#### Scenario: PR description is edited while the matrix is running

- **GIVEN** a CI matrix run is in progress for a pull request head
- **WHEN** the pull request title or body is edited
- **THEN** no new CI matrix run is created and the in-progress run is not cancelled
- **AND** the release guards workflow runs against the edited metadata

#### Scenario: New commits still run the full matrix

- **WHEN** a pull request receives new commits
- **THEN** the CI matrix workflow runs for the new head
- **AND** the superseded run for the same ref is cancelled by the concurrency group

#### Scenario: Release guard contexts keep their names

- **WHEN** the release guards run from their own workflow
- **THEN** they report as `Beta release guard` and `Stable release guard`
- **AND** the branch ruleset's required contexts are unchanged
