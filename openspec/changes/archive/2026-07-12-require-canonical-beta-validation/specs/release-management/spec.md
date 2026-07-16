## MODIFIED Requirements

### Requirement: Merged beta release PRs publish GitHub prereleases

When a pull request from a `release/beta-*` branch is merged into `main`, the release automation SHALL require `RELEASE_PLEASE_TOKEN` rather than falling back to `GITHUB_TOKEN`, verify that all release-managed version files agree on a beta version, create the matching `vX.Y.Z-beta.N` tag at the merge commit, and publish a GitHub prerelease for that tag. Re-running the workflow after the tag already exists SHALL be safe and SHALL NOT create a second tag. Before merge, the beta release guard SHALL require release-candidate validation evidence for canonical `release/beta-X.Y.Z-beta.N` pull requests whose checked-out tree already contains the matching beta version, even when the release-managed version files are unchanged relative to the base branch.

#### Scenario: beta PR merge publishes a prerelease tag

- **GIVEN** a merged pull request from `release/beta-1.19.0-beta.1`
- **AND** release-managed files all contain `1.19.0-beta.1`
- **AND** `RELEASE_PLEASE_TOKEN` is configured
- **WHEN** the beta publish workflow runs
- **THEN** it creates tag `v1.19.0-beta.1` at the merge commit
- **AND** it creates a GitHub prerelease for `v1.19.0-beta.1`

#### Scenario: canonical beta PR with unchanged metadata still requires validation

- **GIVEN** `main` already contains release-managed files set to `1.20.0-beta.3`
- **AND** a pull request from `release/beta-1.20.0-beta.3` targets `main`
- **WHEN** the beta release guard evaluates the pull request before merge
- **THEN** it requires release-candidate validation evidence for the pull request head SHA
- **AND** it fails while that evidence is missing, even though the release-managed version files are unchanged relative to `main`
