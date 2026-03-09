## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, export button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Batch account import

- **WHEN** a user clicks the import button and uploads one or more `auth.json` files
- **THEN** the app calls `POST /api/accounts/import/batch`
- **AND** the response reports imported and failed files independently
- **AND** the account list is refreshed when at least one file imports successfully

#### Scenario: Import refreshes expired access token

- **WHEN** an uploaded `auth.json` contains an expired access token
- **AND** the refresh token is still valid
- **THEN** the backend refreshes the token set before persisting the account

#### Scenario: Export current auth payload archive

- **WHEN** a user clicks the export button
- **THEN** the app downloads a zip archive containing one current `auth.json` payload per stored account
