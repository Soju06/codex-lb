## ADDED Requirements

### Requirement: API-key account usage groups deleted accounts
The API-key account usage breakdown MUST group request-log rows marked as deleted under a single `Deleted Accounts` entry instead of exposing a deleted account id or falling back to an unretrievable account email.

#### Scenario: Deleted account usage is grouped
- **WHEN** request logs for an API key are marked as deleted because their account was deleted
- **THEN** `GET /api/api-keys/{id}/account-usage-7d` includes their cost, token, and request totals under `displayName: "Deleted Accounts"`
- **AND** the deleted-account totals are separate from unrelated unknown-account request logs
