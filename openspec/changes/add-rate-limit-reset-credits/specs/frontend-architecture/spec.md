## ADDED Requirements

### Requirement: Accounts UI shows available reset counts

The accounts and dashboard UI SHALL display the current available reset count from shared account payloads.

Shared account summary payloads exposed to the Accounts page and dashboard UI SHALL include `availableResetCount`.

The Accounts page SHALL support a sort mode that orders accounts by available reset count, using the existing sort tie-break behavior afterward.

When an account summary has `availableResetCount > 0`, account cards and account list items SHALL show a per-account badge for that count.

#### Scenario: Account detail shows disabled reset control

- **GIVEN** an account summary includes `availableResetCount = 2`
- **WHEN** the operator opens the account detail panel
- **THEN** the actions area includes a disabled `Reset (2)` button

#### Scenario: Shared account summary payload exposes available reset count

- **WHEN** the Accounts page or dashboard UI receives an account summary payload for an account with available reset credits
- **THEN** that payload includes `availableResetCount`
- **AND** the UI uses that field for reset-count rendering without requiring a separate reset-credit detail fetch

#### Scenario: Accounts navigation shows aggregate count

- **GIVEN** one or more account summaries have `availableResetCount > 0`
- **WHEN** the application header renders
- **THEN** the `Accounts` navigation tab shows an aggregate count badge

#### Scenario: Accounts page sorts by available reset count

- **GIVEN** two or more account summaries have different `availableResetCount` values
- **WHEN** the operator selects the available-reset sort mode on the Accounts page
- **THEN** the account with the higher available reset count sorts ahead of the lower-count account

#### Scenario: Account cards and list items show per-account badges

- **GIVEN** an account summary has `availableResetCount = 2`
- **WHEN** the account appears in an account card or account list item
- **THEN** the UI shows a badge with count `2` for that account
