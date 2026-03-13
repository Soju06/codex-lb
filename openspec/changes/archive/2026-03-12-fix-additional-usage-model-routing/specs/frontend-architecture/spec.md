## ADDED Requirements

### Requirement: Accounts page renders mapped additional quota labels
The Accounts page MUST render known additional quotas with their mapped user-facing label from canonical quota metadata instead of depending on raw upstream `limitName` strings.

#### Scenario: Codex Spark quota uses mapped label after alias drift
- **WHEN** an account summary contains an additional quota whose canonical key corresponds to `gpt-5.3-codex-spark`
- **AND** the raw upstream `limitName` has changed from an earlier alias
- **THEN** the Accounts page renders the quota label as `GPT-5.3-Codex-Spark`
