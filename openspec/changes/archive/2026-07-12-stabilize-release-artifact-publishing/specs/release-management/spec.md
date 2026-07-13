# release-management Specification Delta

## ADDED Requirements

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
