## ADDED Requirements

### Requirement: Merge Helm and CI support while preserving fork release policy

Upstream Helm chart and CI support changes MUST be reconciled with the fork's release policy. The merged tree MUST keep `codex-lb-cinamon` package/repository metadata, release-please configuration, and publish workflow expectations unless a separate release change explicitly renames them. The merged fork version MUST match upstream `1.16.0`.

#### Scenario: Package metadata remains fork-specific

- **WHEN** `pyproject.toml`, lockfiles, and release configuration are resolved after the merge
- **THEN** the Python package name, CLI entry points, repository URLs, and release-please manifest remain aligned with the fork
- **AND** the version surfaces are aligned to `1.16.0`
- **AND** upstream version changes are not copied in a way that reverts the package identity to upstream defaults

#### Scenario: Helm render checks cover merged chart policy

- **WHEN** Helm validation runs after the merge
- **THEN** it covers the merged Kubernetes support baseline and External Secrets failure/success paths
- **AND** any fork-specific deployment values remain renderable
