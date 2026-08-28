# github-automation delta

## MODIFIED Requirements

### Requirement: Codex review label sync write-token fallback

The `Codex review labels` workflow MUST execute the label synchronization script from the trusted default branch and MUST prefer a dedicated GitHub App installation token, then a repository-provided write token, before falling back to the default `github.token`.

#### Scenario: GitHub App credentials are configured

- **WHEN** the repository defines the `CODEX_LABEL_SYNC_APP_ID` variable and the `CODEX_LABEL_SYNC_APP_PRIVATE_KEY` secret
- **THEN** the workflow mints a short-lived installation token for that App before the sync step
- **AND** the mint requests only the label-sync permission subset (actions write, checks read, contents read, issues write, pull requests read, statuses read) rather than inheriting all installation permissions
- **AND** the sync step uses that token ahead of `CODEX_LABEL_SYNC_TOKEN`, `RELEASE_PLEASE_TOKEN`, and `github.token`

#### Scenario: App token mint fails or is not configured

- **WHEN** the mint step fails, or the `CODEX_LABEL_SYNC_APP_ID` variable is absent
- **THEN** the job does not fail because of the mint step
- **AND** the sync step falls back to the next available token in the chain

#### Scenario: Privileged token is configured

- **WHEN** the workflow synchronizes Codex review labels and no App token was minted
- **THEN** it uses `CODEX_LABEL_SYNC_TOKEN` when present
- **AND** it falls back to `RELEASE_PLEASE_TOKEN` before `github.token`
- **AND** it checks out the default branch with persisted checkout credentials disabled
