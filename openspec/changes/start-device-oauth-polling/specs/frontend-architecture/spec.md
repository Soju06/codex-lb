## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: Device OAuth start begins polling

- **WHEN** the app starts Device Code OAuth with `POST /api/oauth/start`
- **AND** the response includes a `deviceAuthId` and `userCode`
- **THEN** the backend starts polling for the device token without requiring a separate `/api/oauth/complete` call
- **AND** a later `/api/oauth/complete` call remains safe and does not start a duplicate polling task

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed
