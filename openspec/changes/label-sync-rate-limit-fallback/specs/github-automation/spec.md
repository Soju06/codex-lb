## MODIFIED Requirements

### Requirement: Codex review label sync write-token fallback

The `Codex review labels` workflow MUST execute the label synchronization script from the trusted default branch and MUST prefer a repository-provided write token before falling back to the default `github.token`. When the active token's API quota is exhausted at runtime, the script MUST switch once to a configured fallback token and retry the failed call instead of failing the run outright.

#### Scenario: Privileged token is configured

- **WHEN** the workflow synchronizes Codex review labels
- **THEN** it uses `CODEX_LABEL_SYNC_TOKEN` when present
- **AND** it falls back to `RELEASE_PLEASE_TOKEN` before `github.token`
- **AND** it checks out the default branch with persisted checkout credentials disabled

#### Scenario: Active token hits its rate limit

- **GIVEN** the workflow provides `github.token` as `GH_FALLBACK_TOKEN`
- **WHEN** a gh call fails with `API rate limit exceeded`
- **THEN** the script switches to the fallback token once and retries the failed call
- **AND** subsequent calls in the run keep using the fallback token

#### Scenario: No usable fallback token

- **WHEN** a gh call fails with `API rate limit exceeded`
- **AND** no fallback token is configured, or it matches the active token, or it is also exhausted
- **THEN** the run fails as a read/classification failure per the existing contract
