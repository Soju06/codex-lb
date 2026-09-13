## ADDED Requirements

### Requirement: Tracked repository-root entries are allowlisted

Every tracked repository-root entry (file or directory, as listed by `git ls-tree --name-only HEAD`) MUST appear in the `allowed` list of the `[root_files]` section in `.github/simplicity-budgets.toml`, and the simplicity-budget check SHALL report each unlisted entry as a violation that names the entry and the escape hatch. A PR that adds an unlisted root entry SHALL be blocked from merge unless the entry is added to the allowlist in the same diff or a maintainer applies the `simplicity-budget-approved` label. When the `[root_files]` section is absent from the budget configuration, the check SHALL be skipped rather than fail.

#### Scenario: Root tree matches the allowlist

- **WHEN** every tracked repository-root entry appears in the `[root_files]` allowlist
- **THEN** the simplicity-budget check passes with no label required

#### Scenario: Stray root file without an allowlist update

- **WHEN** a PR commits a new repository-root file without adding it to the `[root_files]` allowlist
- **AND** no `simplicity-budget-approved` label is present
- **THEN** the simplicity-budget check fails with a violation naming that file and the escape hatch, and the PR is blocked from merge

#### Scenario: Intentional root entry added with the allowlist in the same diff

- **WHEN** a PR adds a repository-root entry and adds it to the `[root_files]` allowlist in the same diff
- **THEN** the simplicity-budget check passes, and the allowlist change is visible to the reviewer

#### Scenario: Stray root entry with maintainer approval

- **GIVEN** a PR whose tracked root entry is not in the allowlist
- **WHEN** a maintainer applies the `simplicity-budget-approved` label
- **THEN** the violation is downgraded to a warning on the PR run and the check passes

#### Scenario: Budget configuration without a root-files section

- **WHEN** the budget configuration has no `[root_files]` section
- **THEN** the simplicity-budget check skips root-entry enforcement and evaluates the remaining budgets unchanged
