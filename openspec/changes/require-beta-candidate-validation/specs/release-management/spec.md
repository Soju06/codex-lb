## MODIFIED Requirements

### Requirement: Beta releases are prepared through release PRs

Beta releases SHALL be prepared by an automatically maintained pull request against `main` that updates the release-managed version files to `X.Y.Z-beta.N`. The beta preparation flow SHALL run after release-please completes and after pushes to `main`, SHALL derive `X.Y.Z` from the open release-please PR branch, and SHALL do nothing when there is no open release-please PR. Beta release PRs SHALL NOT update `.github/release-please-manifest.json` because stable version ownership remains with release-please.

#### Scenario: automation syncs the next beta from the release-please PR

- **GIVEN** release-please has opened or updated `release-please--branches--main` with `pyproject.toml` version `1.19.0`
- **WHEN** the beta PR sync workflow runs
- **THEN** it creates or updates a pull request that sets release-managed files to `1.19.0-beta.N`
- **AND** `N` is one higher than the highest existing `v1.19.0-beta.N` tag
- **AND** `.github/release-please-manifest.json` remains unchanged

#### Scenario: automation is idle without a release-please PR

- **GIVEN** there is no open release-please PR targeting `main`
- **WHEN** the beta PR sync workflow runs
- **THEN** it exits without creating a beta release pull request

#### Scenario: automation ignores forked release-please branch names

- **GIVEN** a fork has an open pull request whose head branch is named `release-please--branches--main`
- **WHEN** the beta PR sync workflow looks for the release-please PR
- **THEN** it ignores that pull request unless the head repository owner is the canonical repository owner
- **AND** it requests enough open pull requests to avoid missing the canonical release-please PR during high-PR-volume periods

#### Scenario: merged beta release already covers main

- **GIVEN** tag `v1.19.0-beta.1` points to `HEAD`
- **AND** release-managed files all contain `1.19.0-beta.1`
- **WHEN** the beta PR sync workflow runs for base version `1.19.0`
- **THEN** it exits without creating `1.19.0-beta.2`

#### Scenario: automation-generated beta PR starts unvalidated

- **GIVEN** the beta PR sync workflow creates or updates `release/beta-1.20.0-beta.3`
- **WHEN** it writes the pull request body
- **THEN** the body includes a `Release-candidate validation` section
- **AND** the section records the exact beta PR head SHA as the validated candidate placeholder
- **AND** backend, frontend, wheel/package, Docker/container, and live upstream/account smoke checklist items start unchecked

### Requirement: Merged beta release PRs publish GitHub prereleases

When a pull request from the canonical repository's `release/beta-*` branch is merged into `main`, the release automation SHALL require `RELEASE_PLEASE_TOKEN` rather than falling back to `GITHUB_TOKEN`, verify that all release-managed version files agree on a beta version, require release-candidate validation evidence for the exact merged pull request head SHA, verify that the published merge commit tree matches that validated head tree, create the matching `vX.Y.Z-beta.N` tag at the merge commit, and publish a GitHub prerelease for that tag. Re-running the workflow after the tag already exists SHALL be safe and SHALL NOT create a second tag.

#### Scenario: beta PR merge publishes a prerelease tag

- **GIVEN** a merged pull request from `release/beta-1.19.0-beta.1`
- **AND** release-managed files all contain `1.19.0-beta.1`
- **AND** the pull request body contains checked release-candidate validation evidence for the exact merged pull request head SHA
- **AND** the merge commit tree matches the validated pull request head tree
- **AND** `RELEASE_PLEASE_TOKEN` is configured
- **WHEN** the beta publish workflow runs
- **THEN** it creates tag `v1.19.0-beta.1` at the merge commit
- **AND** it creates a GitHub prerelease for `v1.19.0-beta.1`

#### Scenario: inconsistent release metadata is blocked

- **GIVEN** a pull request changes one or more release-managed version files
- **AND** the release-managed files do not all contain the same version
- **WHEN** the CI beta release guard runs
- **THEN** it fails before deciding whether the change is stable or beta
- **AND** it reports the mismatched release-managed file versions

#### Scenario: non-canonical beta metadata PR is blocked

- **GIVEN** a pull request changes release-managed files to `1.20.0-beta.3`
- **AND** the pull request head branch is `fix/pr-938-release-ci`
- **WHEN** the CI beta release guard runs
- **THEN** it fails before the pull request can satisfy the required CI rollup
- **AND** it reports that the expected head branch is `release/beta-1.20.0-beta.3`

#### Scenario: forked beta metadata PR is blocked

- **GIVEN** a pull request changes release-managed files to `1.20.0-beta.3`
- **AND** the pull request head branch is `release/beta-1.20.0-beta.3`
- **BUT** the pull request head repository is a fork rather than the canonical repository
- **WHEN** the CI beta release guard or beta publish guard runs
- **THEN** it fails before the pull request can merge or publish
- **AND** it reports the expected and actual head repositories

#### Scenario: beta publish refuses missing validation evidence

- **GIVEN** a merged pull request from `release/beta-1.20.0-beta.3`
- **AND** release-managed files all contain `1.20.0-beta.3`
- **BUT** the pull request body lacks checked release-candidate validation evidence for the exact pull request head SHA
- **WHEN** the beta publish workflow runs
- **THEN** it fails before creating the `v1.20.0-beta.3` tag
- **AND** it does not create a GitHub prerelease or publish artifacts

#### Scenario: beta publish refuses a stale validated tree

- **GIVEN** a merged pull request from `release/beta-1.20.0-beta.3`
- **AND** the pull request body contains checked release-candidate validation evidence for the pull request head SHA
- **BUT** the merge commit tree differs from the validated pull request head tree
- **WHEN** the beta publish workflow runs
- **THEN** it fails before creating the `v1.20.0-beta.3` tag
- **AND** it reports that the beta PR must be updated onto the final base and revalidated
