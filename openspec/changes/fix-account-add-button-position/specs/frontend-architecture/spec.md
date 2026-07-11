## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate). The browser OAuth stage SHALL show an authorization URL with a copy action that remains functional in secure and non-secure contexts.

The Accounts page SHALL keep the add account button outside the scrollable account list so it remains reachable without scrolling through existing accounts.

The Accounts page SHALL keep long account lists in a bounded internal scroll region on desktop so account rows do not push the page layout past the selected-account detail panel.

The Accounts page SHALL also allow exporting a selected account as an OpenCode-compatible `auth.json` payload with explicit raw-token warnings.

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: Add account remains outside account list scrolling

- **WHEN** the Accounts page renders the account list controls
- **THEN** the add account button is not a child of the scrollable account list
- **AND** the button remains available without scrolling through existing accounts

#### Scenario: Long account list scrolls inside the left panel

- **WHEN** the Accounts page renders more account rows than fit in the visible left panel
- **THEN** the account rows scroll inside the account list region
- **AND** the add account action remains visible outside that scroll region

#### Scenario: OAuth browser authorization URL copy fallback

- **WHEN** a user clicks Copy for the browser authorization URL inside the OAuth dialog
- **THEN** the copy operation succeeds using secure Clipboard API when available
- **AND** falls back to dialog-scoped `execCommand("copy")` when secure Clipboard API is unavailable or blocked

#### Scenario: OAuth browser authorization URL copy failure feedback

- **WHEN** both clipboard copy paths fail for the browser authorization URL inside the OAuth dialog
- **THEN** the dialog surfaces a visible copy failure message

#### Scenario: Device OAuth start begins polling

- **WHEN** the app starts Device Code OAuth with `POST /api/oauth/start`
- **AND** the response includes a `deviceAuthId` and `userCode`
- **THEN** the backend starts polling for the device token without requiring a separate `/api/oauth/complete` call
- **AND** a later `/api/oauth/complete` call remains safe and does not start a duplicate polling task

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed

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

#### Scenario: Export selected account from dashboard

- **WHEN** a user clicks the OpenCode export action for a selected account
- **THEN** the dashboard requests a per-account export from the backend
- **AND** shows copy/download controls for the official OpenCode `auth.json` payload
- **AND** warns that the payload contains raw account tokens
