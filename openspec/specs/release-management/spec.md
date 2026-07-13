# release-management Specification

## Purpose
TBD - created by archiving change add-beta-release-channel. Update Purpose after archive.
## Requirements
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

When a pull request from a `release/beta-*` branch is merged into `main`, the release automation SHALL require `RELEASE_PLEASE_TOKEN` rather than falling back to `GITHUB_TOKEN`, verify that all release-managed version files agree on a beta version, require release-candidate validation evidence for the exact merged pull request head SHA, verify that the published merge commit tree matches that validated head tree, create the matching `vX.Y.Z-beta.N` tag at the merge commit, and publish a GitHub prerelease for that tag. Re-running the workflow after the tag already exists SHALL be safe and SHALL NOT create a second tag. Before merge, the beta release guard SHALL require release-candidate validation evidence for canonical `release/beta-X.Y.Z-beta.N` pull requests whose checked-out tree already contains the matching beta version, even when the release-managed version files are unchanged relative to the base branch.

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

#### Scenario: canonical beta PR with unchanged metadata still requires validation

- **GIVEN** `main` already contains release-managed files set to `1.20.0-beta.3`
- **AND** a pull request from `release/beta-1.20.0-beta.3` targets `main`
- **WHEN** the beta release guard evaluates the pull request before merge
- **THEN** it requires release-candidate validation evidence for the pull request head SHA
- **AND** it fails while that evidence is missing, even though the release-managed version files are unchanged relative to `main`

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

### Requirement: Stable release promotions guard every release-managed version field

Stable release promotion pull requests SHALL fail CI unless every release-managed version field agrees on the stable version and every field that previously held the prior release train version advances together. The guarded fields SHALL include `pyproject.toml`, `app/__init__.py`, `frontend/package.json`, both Helm chart version fields, and the editable `codex-lb` entry in `uv.lock`.

#### Scenario: release-please stable PR misses uv.lock

- **GIVEN** a beta-tested release train has release-managed files at `1.20.0-beta.3`
- **AND** a release-please stable PR changes `pyproject.toml`, `app/__init__.py`, `frontend/package.json`, and Helm chart versions to `1.20.0`
- **BUT** leaves `uv.lock` at `1.20.0-beta.3`
- **WHEN** CI evaluates the stable release guard
- **THEN** the guard fails before the PR can merge
- **AND** the failure identifies `uv.lock` as a release-managed version field that must be updated

#### Scenario: release-please stable PR updates all release-managed fields

- **GIVEN** a beta-tested release train has release-managed files at `1.20.0-beta.3`
- **WHEN** a release-please stable PR changes all release-managed version fields to `1.20.0`
- **THEN** the stable release guard passes

### Requirement: Failed release publishing withdraws public release metadata

If the Release workflow is triggered by a public GitHub Release event and any required publishing job fails, the workflow SHALL make that GitHub Release draft again before exiting. This prevents `/releases/latest` and dashboard update checks from advertising a version whose PyPI, Docker, or Helm artifacts are incomplete.

#### Scenario: stable release workflow fails before artifacts publish

- **GIVEN** GitHub Release `v1.20.0` was published and triggered the Release workflow
- **AND** the workflow fails before PyPI, Docker, and Helm artifacts are all published
- **WHEN** the failure cleanup job runs
- **THEN** the GitHub Release is changed back to draft
- **AND** the release no longer appears as the public latest release

