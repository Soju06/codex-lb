## MODIFIED Requirements

### Requirement: Account management page supports account import and OAuth add flows

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Account import
- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: OAuth add account
- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: OAuth reauth refreshes the existing ChatGPT identity row
- **GIVEN** a local account row already has a non-empty upstream `chatgpt_account_id`
- **AND** the account is re-authenticated through the dashboard OAuth flow
- **WHEN** the new OAuth token payload carries the same upstream `chatgpt_account_id`
- **THEN** the service updates the existing local row instead of creating a duplicate account row
- **AND** the refreshed row is active and carries the latest OAuth tokens and account metadata

#### Scenario: Concurrent OAuth reauth completions do not create duplicate rows
- **GIVEN** two OAuth reauth completions for the same upstream `chatgpt_account_id` finish concurrently
- **WHEN** both completions persist their token payloads
- **THEN** exactly one local account row exists for that upstream identity
