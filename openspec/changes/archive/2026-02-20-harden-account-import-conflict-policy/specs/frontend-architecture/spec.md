## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: Ambiguous duplicate identity import conflict

- **WHEN** `importWithoutOverwrite` was previously enabled and duplicate accounts with the same email exist
- **AND** overwrite mode is enabled again
- **AND** a new import matches multiple existing accounts by email without an exact ID match
- **THEN** `POST /api/accounts/import` returns `409` with `error.code=duplicate_identity_conflict`
- **AND** no existing account is modified

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed
