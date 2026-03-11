## ADDED Requirements

### Requirement: Accounts page renders mapped additional quota labels
The Accounts page MUST render known additional quota limits with their mapped user-facing label instead of exposing only the raw persisted `limitName` key.

#### Scenario: Codex Spark quota uses mapped label
- **WHEN** an account summary contains an additional quota whose `limitName` is the canonical key for `gpt-5.3-codex-spark`
- **THEN** the Accounts page renders the quota label as `GPT-5.3-Codex-Spark`
