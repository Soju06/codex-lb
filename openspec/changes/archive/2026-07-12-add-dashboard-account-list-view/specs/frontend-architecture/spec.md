## ADDED Requirements

### Requirement: Dashboard accounts section supports card and list views

The Dashboard Accounts section SHALL allow operators to choose between the existing card layout and a compact list layout. The default mode SHALL remain cards. The selected account view mode SHALL persist locally and apply on later dashboard visits.

The list layout SHALL use the same dashboard overview account collection as the card layout and SHALL expose account identity, status, plan, quota remaining, credits, limit warm-up state, and the same account actions available from the card layout. The list quota cells SHALL include compact visual meters for each rendered quota row while preserving numeric percent and reset timing text. The Account, Status, Plan, Quota, Credits, and Warm-up list headers SHALL be clickable sort controls. The selected list sort column and direction SHALL persist locally and apply on later dashboard visits that render the compact list layout.

#### Scenario: Dashboard defaults to card view

- **WHEN** the account view-mode preference is unset
- **THEN** the Dashboard Accounts section renders account cards
- **AND** the card/list control indicates card mode is selected

#### Scenario: Operator switches to list view

- **WHEN** an operator selects list mode in the Dashboard Accounts section
- **THEN** the account cards are replaced by a compact list of the same accounts
- **AND** the list exposes each account's status, quota, credits, warm-up state, and available actions
- **AND** each quota row includes a compact visual remaining-capacity meter

#### Scenario: Operator sorts account list columns

- **WHEN** an operator clicks a sortable list header
- **THEN** the account list sorts by that column in ascending order
- **AND** clicking the same header again toggles the sort direction
- **AND** the active sort header exposes its sort direction to assistive technology

#### Scenario: Account list sort persists locally

- **WHEN** an operator sorts the compact account list by a column and direction
- **AND** later returns to the dashboard in the same browser profile with list mode selected
- **THEN** the compact account list renders with the same active sort column and direction

#### Scenario: Account view mode persists locally

- **WHEN** an operator selects list mode
- **AND** later returns to the dashboard in the same browser profile
- **THEN** the Dashboard Accounts section renders in list mode without requiring another selection
