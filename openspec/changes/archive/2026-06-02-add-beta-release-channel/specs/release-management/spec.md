## ADDED Requirements

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

### Requirement: Merged beta release PRs publish GitHub prereleases

When a pull request from a `release/beta-*` branch is merged into `main`, the release automation SHALL require `RELEASE_PLEASE_TOKEN` rather than falling back to `GITHUB_TOKEN`, verify that all release-managed version files agree on a beta version, create the matching `vX.Y.Z-beta.N` tag at the merge commit, and publish a GitHub prerelease for that tag. Re-running the workflow after the tag already exists SHALL be safe and SHALL NOT create a second tag.

#### Scenario: beta PR merge publishes a prerelease tag

- **GIVEN** a merged pull request from `release/beta-1.19.0-beta.1`
- **AND** release-managed files all contain `1.19.0-beta.1`
- **AND** `RELEASE_PLEASE_TOKEN` is configured
- **WHEN** the beta publish workflow runs
- **THEN** it creates tag `v1.19.0-beta.1` at the merge commit
- **AND** it creates a GitHub prerelease for `v1.19.0-beta.1`

### Requirement: Prerelease artifacts do not advance stable aliases

The release publishing workflow SHALL accept both stable tags (`vX.Y.Z`) and prerelease tags (`vX.Y.Z-alpha.N`, `vX.Y.Z-beta.N`, `vX.Y.Z-rc.N`). For prerelease tags, Docker publishing SHALL NOT update `latest`, `X`, or `X.Y` aliases, and the GitHub Release SHALL remain marked as a prerelease and not latest. Stable tags SHALL retain existing stable aliases and latest-release behavior.

#### Scenario: beta release publishes beta-only Docker tags

- **GIVEN** release tag `v1.19.0-beta.1`
- **WHEN** the release publishing workflow builds the Docker image
- **THEN** it publishes the exact version tag `1.19.0-beta.1`
- **AND** it MAY publish channel tag `beta`
- **AND** it MUST NOT publish or update `latest`, `1`, or `1.19`

### Requirement: Stable release promotion remains release-please owned

A beta-tested release train SHALL be promoted by merging the normal release-please stable release PR for the corresponding base version. Stable promotion SHALL rebuild PyPI, Docker, Helm, and GitHub Release artifacts with the stable version instead of retagging prerelease artifacts.

#### Scenario: beta train is promoted to stable

- **GIVEN** `v1.19.0-beta.2` was published from `main`
- **AND** release-please has prepared the stable release PR for `1.19.0`
- **WHEN** the stable release PR is merged
- **THEN** release-please creates the stable `v1.19.0` release
- **AND** the release publishing workflow publishes stable artifacts for `1.19.0`
- **AND** stable Docker aliases `latest`, `1`, and `1.19` are updated only by the stable release
