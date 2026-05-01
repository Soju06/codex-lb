## ADDED Requirements

### Requirement: Per-account OpenCode auth export
The system SHALL let an authenticated dashboard user export one selected account as an OpenCode-compatible `auth.json` payload.

The exported payload SHALL be provider-keyed with exactly one `openai` OAuth entry containing `type`, `refresh`, `access`, `expires`, and `accountId` fields.

The exported payload SHALL NOT include codex-lb-only account metadata, dashboard settings, API keys, request logs, usage history, or multi-account custom fields.

#### Scenario: Export selected account for stock OpenCode
- **WHEN** an authenticated dashboard user exports account `acc_123`
- **THEN** the response includes an `authJson` object with an `openai` OAuth entry
- **AND** `authJson.openai.access` is the decrypted access token for `acc_123`
- **AND** `authJson.openai.refresh` is the decrypted refresh token for `acc_123`
- **AND** `authJson.openai.accountId` is the account's ChatGPT account id when available
- **AND** `authJson.openai.expires` is a non-negative integer in epoch milliseconds

#### Scenario: Export missing account
- **WHEN** an authenticated dashboard user exports an unknown account id
- **THEN** the system returns a dashboard 404 error with code `account_not_found`

#### Scenario: Audit without token material
- **WHEN** an account export succeeds
- **THEN** the system records an audit event identifying the exported account
- **AND** the audit event does not include access or refresh token values
