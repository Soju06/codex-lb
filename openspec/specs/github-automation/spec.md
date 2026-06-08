# github-automation Specification

## Purpose
TBD - created by archiving change fix-codex-review-label-token. Update Purpose after archive.
## Requirements
### Requirement: Codex review label sync write-token fallback

The `Codex review labels` workflow MUST execute the label synchronization script from the trusted default branch and MUST prefer a repository-provided write token before falling back to the default `github.token`.

#### Scenario: Privileged token is configured

- **WHEN** the workflow synchronizes Codex review labels
- **THEN** it uses `CODEX_LABEL_SYNC_TOKEN` when present
- **AND** it falls back to `RELEASE_PLEASE_TOKEN` before `github.token`
- **AND** it checks out the default branch with persisted checkout credentials disabled

### Requirement: Codex review label sync write-denial resilience

The Codex label synchronization script MUST distinguish GitHub write-permission denials from classification/read failures.

#### Scenario: GitHub App token cannot mutate a PR resource

- **WHEN** a label, comment, or workflow-run approval write returns `Resource not accessible by integration (HTTP 403)`
- **THEN** the workflow logs a per-PR warning for the skipped mutation
- **AND** it continues processing remaining selected PRs
- **AND** it exits successfully if no read/classification errors occurred

#### Scenario: PR state cannot be read or classified

- **WHEN** the script cannot read required PR state, check state, merge state, or Codex review evidence
- **THEN** the workflow fails rather than silently treating the PR as synchronized

