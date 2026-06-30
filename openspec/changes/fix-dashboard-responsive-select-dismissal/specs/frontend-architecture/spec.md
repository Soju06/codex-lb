## MODIFIED Requirements

### Requirement: Accounts page

The Accounts page SHALL display a responsive account-management layout with a
searchable account list, import/add-account controls, and selected account
details including usage, token info, proxy binding, and account actions. The
layout SHALL fit mobile, tablet, and desktop dashboard widths without
horizontal page overflow caused by fixed-width account controls.

The Accounts page SHALL keep the add account button outside the scrollable
account list so it remains reachable without scrolling through existing
accounts.

The Accounts page SHALL also allow exporting a selected account as an
OpenCode-compatible `auth.json` payload with explicit raw-token warnings.

#### Scenario: Responsive account management layout

- **WHEN** the Accounts page is rendered at a mobile-width viewport
- **THEN** the account list and selected account detail stack vertically
- **AND** account list filters, quota rows, proxy controls, routing policy
  controls, token status, and action buttons fit within the viewport without
  horizontal document overflow

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account
  list on success

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: Add account remains outside account list scrolling

- **WHEN** the Accounts page renders the account list controls
- **THEN** the add account button is not a child of the scrollable account list
- **AND** the button remains available without scrolling through existing
  accounts

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed

## ADDED Requirements

### Requirement: API key edit dialog

The API key edit dialog SHALL allow operators to update restrictions and
lifecycle settings without accidental dismissal from nested menu interactions.
Clicking outside the dialog SHALL still dismiss the dialog when no nested
dashboard menu surface is involved.

#### Scenario: Nested select interactions do not dismiss the edit dialog

- **WHEN** an operator opens the API key edit dialog
- **AND** chooses an item from a select, model selector, account selector,
  popover, or calendar surface rendered outside the dialog content
- **THEN** the edit dialog remains open with the selected value preserved

#### Scenario: Outside click still dismisses the edit dialog

- **WHEN** an operator clicks outside the API key edit dialog and outside any
  nested dashboard menu surface
- **THEN** the edit dialog closes
