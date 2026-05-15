### Requirement: API-key account usage groups deleted accounts
The API-key account usage breakdown MUST group request-log rows marked as deleted under a single `Deleted Accounts` entry instead of exposing a deleted account id or falling back to an unretrievable account email.

#### Scenario: Deleted request-log rows are grouped in API-key usage
- **WHEN** request logs for an API key are marked as deleted because their account was deleted
- **THEN** `GET /api/api-keys/{id}/account-usage-7d` includes their cost, token, and request totals under `displayName: "Deleted Accounts"`
- **AND** the deleted-account totals are separate from unrelated unknown-account request logs

### Requirement: Deleted account usage uses the dashboard used color
The API-key account-usage donut MUST render the `Deleted Accounts` slice and legend marker with the same color used by the dashboard donut `Used` segment.

#### Scenario: Deleted account usage color matches dashboard used
- **WHEN** `GET /api/api-keys/{id}/account-usage-7d` includes a `Deleted Accounts` group
- **THEN** the Accounts Cost donut renders that group with the same neutral gray used by the dashboard donut `Used` legend row
