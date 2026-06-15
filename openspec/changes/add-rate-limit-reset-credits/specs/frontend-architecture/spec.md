## ADDED Requirements

### Requirement: Accounts UI shows available reset counts

The accounts and dashboard UI SHALL display the current available reset count from shared account payloads.

Shared account summary payloads exposed to the Accounts page and dashboard UI SHALL include `availableResetCount` and `nearestResetExpiryAt`.

The Accounts page SHALL support a sort mode that orders accounts by available reset count, using the existing sort tie-break behavior afterward.

The dashboard account table and account card SHALL NOT show a reset-count badge next to the status. Instead, the dashboard SHALL show a `Reset (N)` ghost button next to the Details action when `availableResetCount > 0`.

The Accounts page list-item boxes SHALL show a circular corner badge at the top-right of each box when `availableResetCount > 0`.

### Requirement: Reset buttons show urgency styling when nearest expiry is imminent

When `nearestResetExpiryAt` is within 7 days of the current time, the Reset button SHALL apply a red border and red text styling to signal urgency.

When `nearestResetExpiryAt` is null, more than 7 days away, or the account has no available credits, the Reset button SHALL use the default ghost style without urgency styling.

#### Scenario: Account detail shows reset control

- **GIVEN** an account summary includes `availableResetCount = 2`
- **WHEN** the operator opens the account detail panel
- **THEN** the actions area includes a ghost `Reset (2)` button next to the Export button

#### Scenario: Shared account summary payload exposes available reset count and nearest expiry

- **WHEN** the Accounts page or dashboard UI receives an account summary payload for an account with available reset credits
- **THEN** that payload includes `availableResetCount`
- **AND** that payload includes `nearestResetExpiryAt`
- **AND** the UI uses those fields for reset-count rendering and urgency styling without requiring a separate reset-credit detail fetch

#### Scenario: Accounts navigation shows aggregate count

- **GIVEN** one or more account summaries have `availableResetCount > 0`
- **WHEN** the application header renders
- **THEN** the `Accounts` navigation tab shows an inline aggregate count badge

#### Scenario: Accounts page sorts by available reset count

- **GIVEN** two or more account summaries have different `availableResetCount` values
- **WHEN** the operator selects the available-reset sort mode on the Accounts page
- **THEN** the account with the higher available reset count sorts ahead of the lower-count account

#### Scenario: Dashboard shows Reset button next to Details

- **GIVEN** a dashboard account summary includes `availableResetCount = 3`
- **WHEN** the account appears in the dashboard account table or account card
- **THEN** a ghost `Reset (3)` button appears next to the Details action
- **AND** no reset-count badge appears next to the account status

#### Scenario: Dashboard hides Reset button when no resets are available

- **GIVEN** a dashboard account summary includes `availableResetCount = 0`
- **WHEN** the account appears in the dashboard account table or account card
- **THEN** no `Reset` button is shown

#### Scenario: Read-only dashboard disables Reset button

- **GIVEN** the dashboard is rendered for a read-only user
- **AND** an account summary includes `availableResetCount = 2`
- **WHEN** the account appears in the dashboard account table or account card
- **THEN** the visible `Reset (2)` button is disabled

#### Scenario: Accounts page list item shows corner reset badge

- **GIVEN** an Accounts page list-item box has `availableResetCount = 2`
- **WHEN** the list item renders
- **THEN** a circular count badge with `2` appears pinned to the top-right corner of the box
- **AND** the badge is hidden when `availableResetCount = 0`

#### Scenario: Reset button shows urgency styling when expiry is imminent

- **GIVEN** an account summary includes `availableResetCount > 0` and `nearestResetExpiryAt` is 3 days from now
- **WHEN** the Reset button renders on the account detail panel, dashboard card, or dashboard list
- **THEN** the button displays a red border and red text styling

#### Scenario: Reset button shows default styling when expiry is not imminent

- **GIVEN** an account summary includes `availableResetCount > 0` and `nearestResetExpiryAt` is 30 days from now
- **WHEN** the Reset button renders
- **THEN** the button uses the default ghost style without red border styling
