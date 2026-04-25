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

#### Scenario: Concurrent browser OAuth sessions stay isolated

- **WHEN** two browser PKCE OAuth sessions are started concurrently from separate dashboard tabs or operators
- **AND** each session later submits its own callback URL
- **THEN** each callback is matched against the flow that minted its `state` token
- **AND** one flow does not invalidate or overwrite the other flow's callback state

#### Scenario: Browser OAuth link refresh

- **WHEN** a user is on the browser PKCE step of the OAuth dialog
- **AND** the current authorization URL has already been used or needs to be replaced
- **THEN** the dialog offers a refresh action that starts the browser OAuth flow again without leaving the dialog
- **AND** the dialog updates to the newly generated authorization URL

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed
